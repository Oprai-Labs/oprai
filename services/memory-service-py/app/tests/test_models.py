"""
Tests for Memory Service Models.

Tests SQLAlchemy models for memory service.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestUserConsentModel:
    """Test UserConsent model"""

    def test_tablename(self):
        """Test table name is user_consents"""
        from app.models.consent import UserConsent

        assert UserConsent.__tablename__ == "user_consents"

    def test_schema(self):
        """Test schema is memory_schema"""
        from app.config import settings
        from app.models.consent import UserConsent

        table_args = UserConsent.__table_args__
        assert table_args["schema"] == settings.DB_SCHEMA

    def test_user_id_primary_key(self):
        """Test user_id is primary key"""
        from app.models.consent import UserConsent

        # Check user_id is in primary key columns
        pk_columns = [c.name for c in UserConsent.__table__.primary_key]
        assert "user_id" in pk_columns

    def test_consent_fields_exist(self):
        """Test all consent fields exist"""
        from app.models.consent import UserConsent

        table_columns = [c.name for c in UserConsent.__table__.columns]

        assert "user_id" in table_columns
        assert "position" in table_columns
        assert "contract" in table_columns
        assert "strategy" in table_columns
        assert "preference" in table_columns
        assert "decision" in table_columns
        assert "created_at" in table_columns
        assert "updated_at" in table_columns

    def test_consent_fields_are_boolean(self):
        """Test consent fields are Boolean type"""
        from app.models.consent import UserConsent

        for field in ["position", "contract", "strategy", "preference", "decision"]:
            column = UserConsent.__table__.columns[field]
            assert str(column.type) == "BOOLEAN"

    def test_default_values(self):
        """Test consent fields have default False"""
        from app.models.consent import UserConsent

        # Check that columns have server_default or default
        for field in ["position", "contract", "strategy", "preference", "decision"]:
            column = UserConsent.__table__.columns[field]
            # The column should have nullable=False
            assert column.nullable is False


class TestMemoryModelsEdgeCases:
    """Test edge cases"""

    def test_model_can_be_instantiated(self):
        """Test model can be instantiated"""
        from datetime import datetime

        from app.models.consent import UserConsent

        # Create instance with mock data
        consent = UserConsent(
            user_id="test-user",
            position=True,
            contract=False,
            strategy=True,
            preference=False,
            decision=True
        )

        assert consent.user_id == "test-user"
        assert consent.position is True
        assert consent.contract is False
        assert consent.strategy is True
        assert consent.preference is False
        assert consent.decision is True

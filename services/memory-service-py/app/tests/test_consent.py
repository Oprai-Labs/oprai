"""
Tests for Consent Service module.

Tests consent CRUD operations and type permission checks.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestConsentFields:
    """Test consent fields constants"""

    def test_consent_fields_defined(self):
        """Test that CONSENT_FIELDS is defined"""
        from app.services.consent import CONSENT_FIELDS

        assert isinstance(CONSENT_FIELDS, tuple)
        assert len(CONSENT_FIELDS) == 5
        assert "position" in CONSENT_FIELDS
        assert "contract" in CONSENT_FIELDS
        assert "strategy" in CONSENT_FIELDS
        assert "preference" in CONSENT_FIELDS
        assert "decision" in CONSENT_FIELDS


class TestGetConsent:
    """Test get_consent function"""

    @pytest.mark.asyncio
    async def test_get_consent_returns_dict(self):
        """Test get_consent returns a dict"""
        from app.services.consent import get_consent

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_consent(mock_db, "user-123")

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_consent_existing_user(self):
        """Test get_consent returns consent flags for existing user"""
        from app.services.consent import get_consent
        from app.models.consent import UserConsent

        mock_db = AsyncMock()
        mock_result = MagicMock()

        # Create a mock row
        mock_row = MagicMock(spec=UserConsent)
        mock_row.position = True
        mock_row.contract = False
        mock_row.strategy = True
        mock_row.preference = True
        mock_row.decision = False

        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_consent(mock_db, "user-123")

        assert result["position"] is True
        assert result["contract"] is False
        assert result["strategy"] is True
        assert result["preference"] is True
        assert result["decision"] is False

    @pytest.mark.asyncio
    async def test_get_consent_no_record(self):
        """Test get_consent returns empty dict when no record"""
        from app.services.consent import get_consent

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_consent(mock_db, "new-user")

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_consent_queries_correct_user(self):
        """Test get_consent queries by user_id"""
        from app.services.consent import get_consent

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        await get_consent(mock_db, "specific-user-456")

        # Verify execute was called
        assert mock_db.execute.called


class TestUpdateConsent:
    """Test update_consent function"""

    @pytest.mark.asyncio
    async def test_update_consent_creates_new(self):
        """Test update_consent creates new record when none exists"""
        from app.services.consent import update_consent
        from app.models.consent import UserConsent

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Mock the refresh to set attributes
        async def mock_refresh(row):
            row.position = True
            row.contract = False
            row.strategy = True
            row.preference = False
            row.decision = True

        mock_db.refresh = AsyncMock(side_effect=mock_refresh)

        result = await update_consent(
            mock_db,
            "user-789",
            {"position": True, "decision": True}
        )

        assert "position" in result
        assert "contract" in result
        assert mock_db.add.called

    @pytest.mark.asyncio
    async def test_update_consent_updates_existing(self):
        """Test update_consent updates existing record"""
        from app.services.consent import update_consent
        from app.models.consent import UserConsent

        mock_db = AsyncMock()
        mock_result = MagicMock()

        # Create existing row
        mock_row = MagicMock(spec=UserConsent)
        mock_row.position = False
        mock_row.contract = False
        mock_row.strategy = False
        mock_row.preference = False
        mock_row.decision = False

        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await update_consent(
            mock_db,
            "user-789",
            {"position": True, "strategy": True}
        )

        assert mock_row.position is True
        assert mock_row.strategy is True

    @pytest.mark.asyncio
    async def test_update_consent_ignores_unknown_fields(self):
        """Test update_consent ignores unknown fields"""
        from app.services.consent import update_consent
        from app.models.consent import UserConsent

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock(spec=UserConsent)
        mock_row.position = False
        mock_row.contract = False
        mock_row.strategy = False
        mock_row.preference = False
        mock_row.decision = False

        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        await update_consent(
            mock_db,
            "user-123",
            {"unknown_field": True, "another_unknown": 123}
        )

        # Unknown fields should not raise and should be ignored

    @pytest.mark.asyncio
    async def test_update_consent_default_values(self):
        """Test update_consent uses defaults for missing fields"""
        from app.services.consent import update_consent
        from app.models.consent import UserConsent

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        added_row = None

        def capture_add(row):
            nonlocal added_row
            added_row = row

        mock_db.add = MagicMock(side_effect=capture_add)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        await update_consent(mock_db, "new-user", {})

        assert added_row is not None
        # Check default values are False
        assert added_row.position is False
        assert added_row.contract is False

    @pytest.mark.asyncio
    async def test_update_consent_returns_full_map(self):
        """Test update_consent returns full consent map"""
        from app.services.consent import update_consent
        from app.models.consent import UserConsent

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock(spec=UserConsent)
        mock_row.position = True
        mock_row.contract = False
        mock_row.strategy = True
        mock_row.preference = False
        mock_row.decision = True

        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await update_consent(
            mock_db,
            "user-123",
            {"position": True}
        )

        assert len(result) == 5
        assert "position" in result
        assert "contract" in result
        assert "strategy" in result
        assert "preference" in result
        assert "decision" in result


class TestIsTypeAllowed:
    """Test is_type_allowed function"""

    def test_meta_always_allowed(self):
        """Test meta type is always allowed"""
        from app.services.consent import is_type_allowed

        empty_consent = {}
        assert is_type_allowed(empty_consent, "meta") is True

        consent_with_false = {
            "position": False,
            "contract": False,
            "strategy": False,
            "preference": False,
            "decision": False,
        }
        assert is_type_allowed(consent_with_false, "meta") is True

    def test_position_allowed_when_true(self):
        """Test position type allowed when consent is True"""
        from app.services.consent import is_type_allowed

        consent = {"position": True}
        assert is_type_allowed(consent, "position") is True

    def test_position_not_allowed_when_false(self):
        """Test position type not allowed when consent is False"""
        from app.services.consent import is_type_allowed

        consent = {"position": False}
        assert is_type_allowed(consent, "position") is False

    def test_position_not_allowed_when_missing(self):
        """Test position type not allowed when not in consent"""
        from app.services.consent import is_type_allowed

        consent = {}
        assert is_type_allowed(consent, "position") is False

    def test_contract_allowed_when_true(self):
        """Test contract type allowed when consent is True"""
        from app.services.consent import is_type_allowed

        consent = {"contract": True}
        assert is_type_allowed(consent, "contract") is True

    def test_contract_not_allowed_when_false(self):
        """Test contract type not allowed when consent is False"""
        from app.services.consent import is_type_allowed

        consent = {"contract": False}
        assert is_type_allowed(consent, "contract") is False

    def test_strategy_allowed_when_true(self):
        """Test strategy type allowed when consent is True"""
        from app.services.consent import is_type_allowed

        consent = {"strategy": True}
        assert is_type_allowed(consent, "strategy") is True

    def test_strategy_not_allowed_when_false(self):
        """Test strategy type not allowed when consent is False"""
        from app.services.consent import is_type_allowed

        consent = {"strategy": False}
        assert is_type_allowed(consent, "strategy") is False

    def test_preference_allowed_when_true(self):
        """Test preference type allowed when consent is True"""
        from app.services.consent import is_type_allowed

        consent = {"preference": True}
        assert is_type_allowed(consent, "preference") is True

    def test_preference_not_allowed_when_false(self):
        """Test preference type not allowed when consent is False"""
        from app.services.consent import is_type_allowed

        consent = {"preference": False}
        assert is_type_allowed(consent, "preference") is False

    def test_decision_allowed_when_true(self):
        """Test decision type allowed when consent is True"""
        from app.services.consent import is_type_allowed

        consent = {"decision": True}
        assert is_type_allowed(consent, "decision") is True

    def test_decision_not_allowed_when_false(self):
        """Test decision type not allowed when consent is False"""
        from app.services.consent import is_type_allowed

        consent = {"decision": False}
        assert is_type_allowed(consent, "decision") is False

    def test_unknown_type_not_allowed(self):
        """Test unknown type is not allowed"""
        from app.services.consent import is_type_allowed

        consent = {"position": True}
        assert is_type_allowed(consent, "unknown_type") is False

    def test_converts_truthy_to_true(self):
        """Test truthy values are converted to True"""
        from app.services.consent import is_type_allowed

        consent = {"position": 1}
        assert is_type_allowed(consent, "position") is True

    def test_converts_falsy_to_false(self):
        """Test falsy values are converted to False"""
        from app.services.consent import is_type_allowed

        consent = {"position": 0}
        assert is_type_allowed(consent, "position") is False


class TestConsentIntegration:
    """Integration tests for consent flow"""

    @pytest.mark.asyncio
    async def test_full_consent_flow(self):
        """Test complete consent update and check flow"""
        from app.services.consent import update_consent, is_type_allowed
        from app.models.consent import UserConsent

        mock_db = AsyncMock()
        mock_result = MagicMock()

        # First call: no existing record
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        added_row = None

        def capture_add(row):
            nonlocal added_row
            added_row = row

        mock_db.add = MagicMock(side_effect=capture_add)
        mock_db.flush = AsyncMock()

        async def mock_refresh(row):
            row.position = True
            row.contract = False
            row.strategy = True
            row.preference = False
            row.decision = True

        mock_db.refresh = AsyncMock(side_effect=mock_refresh)

        # Update consent
        consent = await update_consent(
            mock_db,
            "user-flow-test",
            {"position": True, "strategy": True, "decision": True}
        )

        # Check types
        assert is_type_allowed(consent, "position") is True
        assert is_type_allowed(consent, "strategy") is True
        assert is_type_allowed(consent, "decision") is True
        assert is_type_allowed(consent, "contract") is False
        assert is_type_allowed(consent, "preference") is False
        # Meta always allowed
        assert is_type_allowed(consent, "meta") is True

    @pytest.mark.asyncio
    async def test_consent_toggle_flow(self):
        """Test toggling consent on/off"""
        from app.services.consent import update_consent, is_type_allowed
        from app.models.consent import UserConsent

        # First: create with True
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        added_row = None

        def capture_add(row):
            nonlocal added_row
            added_row = row

        mock_db.add = MagicMock(side_effect=capture_add)
        mock_db.flush = AsyncMock()

        async def mock_refresh(row):
            row.position = True
            row.contract = False
            row.strategy = False
            row.preference = False
            row.decision = False

        mock_db.refresh = AsyncMock(side_effect=mock_refresh)

        consent = await update_consent(mock_db, "user-toggle", {"position": True})
        assert is_type_allowed(consent, "position") is True

        # Second: update to False - need new mock setup for existing row
        mock_db2 = AsyncMock()
        mock_result2 = MagicMock()
        existing_row = MagicMock(spec=UserConsent)
        existing_row.position = True  # Current value in DB
        existing_row.contract = False
        existing_row.strategy = False
        existing_row.preference = False
        existing_row.decision = False
        mock_result2.scalar_one_or_none.return_value = existing_row
        mock_db2.execute = AsyncMock(return_value=mock_result2)
        mock_db2.flush = AsyncMock()
        mock_db2.refresh = AsyncMock()

        consent2 = await update_consent(mock_db2, "user-toggle", {"position": False})

        # Verify the row was updated
        assert existing_row.position is False
        assert is_type_allowed(consent2, "position") is False

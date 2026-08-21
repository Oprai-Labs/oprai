"""
Tests for Memory Service Authentication Middleware.

Tests require_auth dependency for wallet and API key validation.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


class TestMemoryAuthRequireAuth:
    """Test require_auth dependency"""

    @pytest.mark.asyncio
    async def test_missing_wallet_raises_401(self):
        """Test missing X-User-Wallet raises 401"""
        from app.middleware.auth import require_auth

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(
                x_user_wallet=None,
                x_internal_api_key="test-key"
            )

        assert exc_info.value.status_code == 401
        assert "wallet header" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_403(self):
        """Test missing X-Internal-Api-Key raises 403"""
        from app.middleware.auth import require_auth

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(
                x_user_wallet="Wallet123",
                x_internal_api_key=None
            )

        assert exc_info.value.status_code == 403
        assert "invalid" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_invalid_api_key_raises_403(self):
        """Test invalid X-Internal-Api-Key raises 403"""
        from app.middleware.auth import require_auth

        with patch("app.middleware.auth.settings") as mock_settings:
            mock_settings.OPRAI_INTERNAL_API_KEY = "correct-key"

            with pytest.raises(HTTPException) as exc_info:
                await require_auth(
                    x_user_wallet="Wallet123",
                    x_internal_api_key="wrong-key"
                )

            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_valid_credentials_returns_wallet(self):
        """Test valid credentials return wallet address"""
        from app.middleware.auth import require_auth

        with patch("app.middleware.auth.settings") as mock_settings:
            mock_settings.OPRAI_INTERNAL_API_KEY = "correct-key"

            result = await require_auth(
                x_user_wallet="Wallet123",
                x_internal_api_key="correct-key"
            )

            assert result == "Wallet123"

    @pytest.mark.asyncio
    async def test_empty_wallet_raises_401(self):
        """Test empty wallet string raises 401"""
        from app.middleware.auth import require_auth

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(
                x_user_wallet="",
                x_internal_api_key="test-key"
            )

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_solana_address(self):
        """Test valid Solana address passes"""
        from app.middleware.auth import require_auth

        # Example valid Solana address
        valid_address = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"

        with patch("app.middleware.auth.settings") as mock_settings:
            mock_settings.OPRAI_INTERNAL_API_KEY = "correct-key"

            result = await require_auth(
                x_user_wallet=valid_address,
                x_internal_api_key="correct-key"
            )

            assert result == valid_address

    @pytest.mark.asyncio
    async def test_api_key_whitespace_raises_403(self):
        """Test whitespace API key raises 403"""
        from app.middleware.auth import require_auth

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(
                x_user_wallet="Wallet123",
                x_internal_api_key="   "
            )

        assert exc_info.value.status_code == 403


class TestMemoryAuthEdgeCases:
    """Test edge cases for auth"""

    @pytest.mark.asyncio
    async def test_none_api_key_with_empty_string(self):
        """Test None vs empty string for API key"""
        from app.middleware.auth import require_auth

        # Both None and empty string should be treated similarly
        with patch("app.middleware.auth.settings") as mock_settings:
            mock_settings.OPRAI_INTERNAL_API_KEY = "test-key"

            # Empty string is falsy
            with pytest.raises(HTTPException) as exc_info:
                await require_auth(
                    x_user_wallet="Wallet123",
                    x_internal_api_key=""
                )

            assert exc_info.value.status_code == 403

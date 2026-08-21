"""
Tests for Database Connection module.

Tests async database connection, session management, and URL handling.
"""

import pytest
from unittest.mock import AsyncMock, patch


class TestBuildAsyncPgUrl:
    """Test _build_asyncpg_url function"""

    def test_convert_postgresql_to_asyncpg(self):
        """Test converting postgresql:// to postgresql+asyncpg://"""
        from app.db.connection import _build_asyncpg_url

        url, connect_args = _build_asyncpg_url("postgresql://localhost:5432/db")

        assert "postgresql+asyncpg://" in url
        assert "localhost" in url

    def test_sslmode_disable(self):
        """Test sslmode=disable is handled"""
        from app.db.connection import _build_asyncpg_url

        url, connect_args = _build_asyncpg_url("postgresql://localhost:5432/db?sslmode=disable")

        assert "sslmode" not in url
        assert connect_args.get("ssl") is False

    def test_sslmode_not_in_url(self):
        """Test sslmode not in url when not specified"""
        from app.db.connection import _build_asyncpg_url

        url, connect_args = _build_asyncpg_url("postgresql://localhost:5432/db")

        assert "sslmode" not in url
        # asyncpg runs behind pgbouncer in transaction mode, which cannot serve
        # prepared statements — hence statement_cache_size=0. Losing it does not
        # fail here; it fails in production, intermittently, under load.
        assert connect_args == {"statement_cache_size": 0, "command_timeout": 30}

    def test_multiple_params(self):
        """Test multiple query parameters"""
        from app.db.connection import _build_asyncpg_url

        url, connect_args = _build_asyncpg_url("postgresql://localhost:5432/db?param1=value1&sslmode=disable")

        assert "param1=value1" in url
        assert "sslmode" not in url
        assert connect_args.get("ssl") is False


class TestDatabaseConnectionEdgeCases:
    """Test edge cases"""

    def test_url_with_special_characters(self):
        """Test URL with special characters in password"""
        from app.db.connection import _build_asyncpg_url

        url, connect_args = _build_asyncpg_url("postgresql://user:p%40ssw0rd@localhost:5432/db")

        assert "p%40ssw0rd" in url

    def test_url_with_port(self):
        """Test URL with non-standard port"""
        from app.db.connection import _build_asyncpg_url

        url, connect_args = _build_asyncpg_url("postgresql://localhost:9999/db")

        assert "9999" in url

    def test_url_with_database_name(self):
        """Test URL with database name"""
        from app.db.connection import _build_asyncpg_url

        url, connect_args = _build_asyncpg_url("postgresql://localhost:5432/my_database")

        assert "my_database" in url

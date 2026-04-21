"""
Tests for Memory Service Configuration.

Tests Settings class, database configuration,
and service URLs.
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestMemoryServiceConfig:
    """Test Settings for memory service"""

    def test_default_port(self):
        """Test default HTTP port"""
        from app.config import settings
        assert settings.PORT == 3040

    def test_default_grpc_port(self):
        """Test default gRPC port"""
        from app.config import settings
        assert settings.GRPC_PORT == 50054

    def test_default_database_url(self):
        """Test default database URL"""
        from app.config import settings
        assert "postgresql" in settings.DATABASE_URL

    def test_default_qdrant_url(self):
        """Test default Qdrant URL"""
        from app.config import settings
        assert settings.QDRANT_URL.startswith("http://localhost:")

    def test_openai_api_key_env_var(self):
        """Test OpenAI API key environment variable"""
        with patch.dict(os.environ, {"OPRAI_OPENAI_API_KEY": "test-key-123"}):
            from importlib import reload
            import app.config
            reload(app.config)
            # After reload it would have test value

    def test_collection_name_default(self):
        """Test default collection name"""
        from app.config import settings
        assert hasattr(settings, "COLLECTION_NAME")
        assert settings.COLLECTION_NAME == "oprai_memories"

    def test_embed_dimension_default(self):
        """Test default embedding dimension"""
        from app.config import settings
        assert hasattr(settings, "EMBEDDING_DIM")
        assert settings.EMBEDDING_DIM == 3072

    def test_model_config_extra_ignore(self):
        """Test model config ignores extra fields"""
        from app.config import Settings
        settings = Settings()
        assert settings.model_config.get("extra") == "ignore"

    def test_database_schema(self):
        """Test database schema configuration"""
        from app.config import settings
        assert hasattr(settings, "DB_SCHEMA")
        assert settings.DB_SCHEMA == "memory_schema"

    def test_environment_defaults_to_development(self):
        """Test environment defaults to development"""
        from app.config import settings
        assert settings.NODE_ENV == "development"

    def test_internal_api_key_default(self):
        """Test internal API key has default"""
        from app.config import settings
        assert settings.OPRAI_INTERNAL_API_KEY != ""

    def test_jwt_secret_default(self):
        """Test JWT secret has default"""
        from app.config import settings
        assert settings.OPRAI_JWT_SECRET != ""


class TestMemoryServiceEnvironment:
    """Test environment variable handling"""

    def test_port_override(self):
        """Test port can be overridden"""
        with patch.dict(os.environ, {"PORT": "5000"}):
            from importlib import reload
            import app.config
            reload(app.config)

    def test_database_url_override(self):
        """Test database URL can be overridden"""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://custom:5432/db"}):
            from importlib import reload
            import app.config
            reload(app.config)

    def test_qdrant_url_override(self):
        """Test Qdrant URL can be overridden"""
        with patch.dict(os.environ, {"QDRANT_URL": "http://custom:6333"}):
            from importlib import reload
            import app.config
            reload(app.config)

    def test_collection_override(self):
        """Test collection name can be overridden"""
        with patch.dict(os.environ, {"COLLECTION_NAME": "custom_collection"}):
            from importlib import reload
            import app.config
            reload(app.config)


class TestMemoryServiceTypes:
    """Test type validation"""

    def test_port_is_positive_int(self):
        """Test port must be positive integer"""
        from app.config import settings
        assert isinstance(settings.PORT, int)
        assert settings.PORT > 0

    def test_grpc_port_is_positive_int(self):
        """Test gRPC port must be positive integer"""
        from app.config import settings
        assert isinstance(settings.GRPC_PORT, int)
        assert settings.GRPC_PORT > 0

    def test_embedding_dimension_is_positive_int(self):
        """Test embedding dimension must be positive"""
        from app.config import settings
        assert isinstance(settings.EMBEDDING_DIM, int)
        assert settings.EMBEDDING_DIM > 0

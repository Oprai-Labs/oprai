"""
Tests for Chat Service Configuration.

Tests Settings class, environment variable loading,
and default values.
"""

import os
import pytest
from unittest.mock import patch


class TestSettings:
    """Test Settings class"""

    def test_default_values(self):
        """Test default settings values"""
        from app.config import Settings

        settings = Settings()

        # Server defaults
        assert settings.PORT == 3020
        assert settings.GRPC_PORT == 50052

        # Database defaults
        assert settings.DATABASE_URL == "postgresql+asyncpg://postgres@localhost:5433/oprai"
        assert settings.DB_SCHEMA == "chat_schema"

        # OpenAI defaults
        assert settings.OPRAI_OPENAI_MODEL == "gpt-4o-mini"
        assert settings.OPRAI_GPT_REASONING_EFFORT == "medium"
        assert settings.OPRAI_GPT_MAX_TOKENS == 4096

        # Inter-service defaults
        assert settings.MEMORY_SERVICE_URL == "http://localhost:3040"
        assert settings.GATEWAY_URL == "http://localhost:3001"
        assert settings.OPRAI_INTERNAL_API_KEY == "dev-internal-key-change"
        assert settings.OPRAI_JWT_SECRET == "dev-insecure-secret-change"

        # Solana RPC
        assert settings.SOLANA_RPC_URL == "https://api.mainnet-beta.solana.com"

        # Environment
        assert settings.NODE_ENV == "development"
        assert settings.CORS_ALLOWED_ORIGINS == ""

    def test_env_variable_override(self):
        """Test environment variables override defaults"""
        with patch.dict(os.environ, {
            "PORT": "4000",
            "OPRAI_OPENAI_MODEL": "gpt-4",
            "NODE_ENV": "production",
        }):
            from app.config import Settings
            settings = Settings()

            assert settings.PORT == 4000
            assert settings.OPRAI_OPENAI_MODEL == "gpt-4"
            assert settings.NODE_ENV == "production"

    def test_settings_instance_exists(self):
        """Test that settings singleton is created"""
        from app.config import settings

        assert settings is not None
        assert isinstance(settings.PORT, int)

    def test_case_insensitive_env(self):
        """Test pydantic-settings is case insensitive"""
        with patch.dict(os.environ, {
            "port": "5000",
            "Oprai_Openai_Api_Key": "test-key",
        }):
            from app.config import Settings
            settings = Settings()

            # Should read both case variations
            assert settings.PORT == 5000

    def test_model_config(self):
        """Test model configuration"""
        from app.config import Settings

        settings = Settings()
        assert settings.model_config.get("extra") == "ignore"

    def test_fallback_model_default(self):
        """Test fallback model has default value"""
        from app.config import Settings

        settings = Settings()
        assert settings.OPRAI_OPENAI_FALLBACK_MODEL == "gpt-4o-mini"

    def test_env_file_loading(self):
        """Test env_file configuration"""
        from app.config import Settings

        settings = Settings()
        config = settings.model_config
        assert "env_file" in config
        assert config["env_file"] == ".env"


class TestSettingsTypes:
    """Test Settings type validation"""

    def test_port_is_int(self):
        """Test PORT is integer"""
        from app.config import Settings
        settings = Settings()

        assert isinstance(settings.PORT, int)
        assert settings.PORT > 0

    def test_max_tokens_is_int(self):
        """Test MAX_TOKENS is integer"""
        from app.config import Settings
        settings = Settings()

        assert isinstance(settings.OPRAI_GPT_MAX_TOKENS, int)
        assert settings.OPRAI_GPT_MAX_TOKENS > 0


class TestSettingsURLs:
    """Test URL configurations"""

    def test_memory_service_url_format(self):
        """Test memory service URL is valid format"""
        from app.config import Settings
        settings = Settings()

        assert settings.MEMORY_SERVICE_URL.startswith("http://") or settings.MEMORY_SERVICE_URL.startswith("https://")

    def test_gateway_url_format(self):
        """Test gateway URL is valid format"""
        from app.config import Settings
        settings = Settings()

        assert settings.GATEWAY_URL.startswith("http://") or settings.GATEWAY_URL.startswith("https://")

    def test_solana_rpc_url_format(self):
        """Test Solana RPC URL is valid format"""
        from app.config import Settings
        settings = Settings()

        assert settings.SOLANA_RPC_URL.startswith("https://")

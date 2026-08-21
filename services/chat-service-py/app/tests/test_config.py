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
        """The values Settings DECLARES, not the ones the environment supplies.

        This test used to read them off an instance. Settings is env-backed, so
        every assertion here quietly became "does this developer's .env happen
        to match" — and once OPRAI_RESPONDER_MODEL_OPENAI and DATABASE_URL were
        set locally, it could not pass on a working machine.
        """
        from app.config import Settings

        def declared(name: str):
            return Settings.model_fields[name].default

        # Server
        assert declared("PORT") == 3020
        assert declared("GRPC_PORT") == 50052

        # Database
        assert declared("DATABASE_URL") == "postgresql+asyncpg://postgres:@localhost:5433/oprai"
        assert declared("DB_SCHEMA") == "chat_schema"

        # LLM
        assert declared("OPRAI_RESPONDER_MODEL_OPENAI") == "gpt-5.4-mini"
        assert declared("OPRAI_GPT_REASONING_EFFORT") == "medium"

        # Inter-service
        assert declared("MEMORY_SERVICE_URL") == "http://localhost:3040"
        assert declared("GATEWAY_URL") == "http://localhost:3001"

        # Secrets carry NO baked-in default — chat-service ships without a
        # usable key rather than with a guessable one. The Go services do hold
        # dev placeholders, but they refuse to boot in production while set;
        # here the empty string is the guarantee, so pin it.
        assert declared("OPRAI_INTERNAL_API_KEY") == ""
        assert declared("OPRAI_JWT_SECRET") == ""

    def test_env_variable_override(self):
        """Test environment variables override defaults"""
        with patch.dict(os.environ, {
            "PORT": "4000",
            "OPRAI_RESPONDER_MODEL_OPENAI": "gpt-4",
            "NODE_ENV": "production",
        }):
            from app.config import Settings
            settings = Settings()

            assert settings.PORT == 4000
            assert settings.OPRAI_RESPONDER_MODEL_OPENAI == "gpt-4"
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
        assert settings.OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI == "gpt-4o-mini"

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

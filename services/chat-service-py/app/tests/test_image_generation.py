"""
Tests for Image Generation module.

Tests image providers and generation service.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


class TestImageProvider:
    """Test ImageProvider enum"""

    def test_provider_values(self):
        """Test all provider values"""
        from app.llm.image_generation import ImageProvider

        assert ImageProvider.OPENAI_DALLE.value == "openai_dalle"
        assert ImageProvider.MIDJOURNEY.value == "midjourney"
        assert ImageProvider.STABILITY_AI.value == "stability_ai"
        assert ImageProvider.REPLICATE.value == "replicate"


class TestImageSize:
    """Test ImageSize enum"""

    def test_size_values(self):
        """Test size values"""
        from app.llm.image_generation import ImageSize

        assert ImageSize.SQUARE.value == "1024x1024"
        assert ImageSize.LANDSCAPE.value == "1792x1024"
        assert ImageSize.PORTRAIT.value == "1024x1792"


class TestImageStyle:
    """Test ImageStyle enum"""

    def test_style_values(self):
        """Test style values"""
        from app.llm.image_generation import ImageStyle

        assert ImageStyle.VIVID.value == "vivid"
        assert ImageStyle.NATURAL.value == "natural"


class TestImageConfig:
    """Test ImageConfig dataclass"""

    def test_config_defaults(self):
        """Test config default values"""
        from app.llm.image_generation import ImageConfig, ImageProvider, ImageSize, ImageStyle

        config = ImageConfig(
            provider=ImageProvider.OPENAI_DALLE,
            prompt="A cat"
        )

        assert config.size == ImageSize.SQUARE
        assert config.style == ImageStyle.VIVID
        assert config.quality == "standard"
        assert config.n == 1
        assert config.steps == 50
        assert config.cfg_scale == 7.0

    def test_config_with_values(self):
        """Test config with custom values"""
        from app.llm.image_generation import ImageConfig, ImageProvider, ImageSize

        config = ImageConfig(
            provider=ImageProvider.STABILITY_AI,
            prompt="A dog",
            size=ImageSize.LANDSCAPE,
            n=2,
            steps=30
        )

        assert config.n == 2
        assert config.steps == 30


class TestGeneratedImage:
    """Test GeneratedImage dataclass"""

    def test_generated_image_defaults(self):
        """Test default values"""
        from app.llm.image_generation import GeneratedImage, ImageProvider

        img = GeneratedImage()

        assert img.url is None
        assert img.base64_data is None
        assert img.provider == ImageProvider.OPENAI_DALLE
        assert img.width == 1024
        assert img.height == 1024

    def test_generated_image_with_data(self):
        """Test with data"""
        from app.llm.image_generation import GeneratedImage, ImageProvider

        img = GeneratedImage(
            url="https://example.com/image.png",
            provider=ImageProvider.STABILITY_AI,
            width=512,
            height=512
        )

        assert img.url == "https://example.com/image.png"
        assert img.width == 512


class TestBaseImageProvider:
    """Test BaseImageProvider abstract class"""

    def test_is_abstract(self):
        """Test BaseImageProvider is abstract"""
        from app.llm.image_generation import BaseImageProvider
        from abc import ABC

        assert issubclass(BaseImageProvider, ABC)


class TestOpenAIDalleProvider:
    """Test OpenAI DALL-E provider"""

    def test_init(self):
        """Test provider initialization"""
        from app.llm.image_generation import OpenAIDalleProvider, ImageProvider

        provider = OpenAIDalleProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == ImageProvider.OPENAI_DALLE
        assert provider.base_url == "https://api.openai.com/v1"

    def test_init_custom_url(self):
        """Test with custom base URL"""
        from app.llm.image_generation import OpenAIDalleProvider

        provider = OpenAIDalleProvider(api_key="key", base_url="https://custom.com/v1")

        assert provider.base_url == "https://custom.com/v1"


class TestStabilityAIProvider:
    """Test Stability AI provider"""

    def test_init(self):
        """Test provider initialization"""
        from app.llm.image_generation import StabilityAIProvider, ImageProvider

        provider = StabilityAIProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == ImageProvider.STABILITY_AI

    def test_default_url(self):
        """Test default URL"""
        from app.llm.image_generation import StabilityAIProvider

        provider = StabilityAIProvider(api_key="key")

        assert provider.base_url == "https://api.stability.ai/v1"


class TestReplicateProvider:
    """Test Replicate provider"""

    def test_init(self):
        """Test provider initialization"""
        from app.llm.image_generation import ReplicateProvider, ImageProvider

        provider = ReplicateProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == ImageProvider.REPLICATE

    def test_default_url(self):
        """Test default URL"""
        from app.llm.image_generation import ReplicateProvider

        provider = ReplicateProvider()

        assert provider.base_url == "https://api.replicate.com/v1"


class TestTogetherAIProvider:
    """Test Together AI provider"""

    def test_init(self):
        """Test provider initialization"""
        from app.llm.image_generation import TogetherAIProvider, ImageProvider

        provider = TogetherAIProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == ImageProvider.TOGETHER_AI


class TestImageGenerationService:
    """Test ImageGenerationService"""

    def test_init(self):
        """Test service initialization"""
        from app.llm.image_generation import ImageGenerationService

        service = ImageGenerationService()

        assert service._default_provider.value == "openai_dalle"
        assert service._providers == {}

    def test_init_with_config(self):
        """Test init with config"""
        from app.llm.image_generation import ImageGenerationService

        service = ImageGenerationService(config={"test": "value"})

        assert service.config == {"test": "value"}

    def test_register_provider(self):
        """Test registering a provider"""
        from app.llm.image_generation import ImageGenerationService, OpenAIDalleProvider, ImageProvider

        service = ImageGenerationService()
        provider = OpenAIDalleProvider(api_key="key")

        service.register_provider(provider)

        assert ImageProvider.OPENAI_DALLE in service._providers

    def test_set_default_provider(self):
        """Test setting default provider"""
        from app.llm.image_generation import ImageGenerationService, ImageProvider

        service = ImageGenerationService()
        service.set_default_provider(ImageProvider.STABILITY_AI)

        assert service._default_provider == ImageProvider.STABILITY_AI

    def test_generate_no_provider(self):
        """Test generate without registered provider"""
        from app.llm.image_generation import ImageGenerationService

        service = ImageGenerationService()

        # No providers registered, should raise
        with pytest.raises(ValueError, match="not available"):
            import asyncio
            asyncio.run(service.generate("A cat"))

    def test_list_providers(self):
        """Test listing providers"""
        from app.llm.image_generation import ImageGenerationService, OpenAIDalleProvider

        service = ImageGenerationService()
        service.register_provider(OpenAIDalleProvider(api_key="key"))

        providers = service.list_providers()

        assert len(providers) == 1


class TestGlobalImageService:
    """Test global image service"""

    def test_get_image_service(self):
        """Test getting image service"""
        from app.llm.image_generation import get_image_service

        # Reset global
        import app.llm.image_generation as img_module
        img_module._image_service = None

        service = get_image_service()

        assert service is not None

    def test_get_image_service_singleton(self):
        """Test singleton behavior"""
        from app.llm.image_generation import get_image_service

        # Reset global
        import app.llm.image_generation as img_module
        img_module._image_service = None

        service1 = get_image_service()
        service2 = get_image_service()

        assert service1 is service2


class TestImageGenerationMethods:
    """Test generation helper methods"""

    @pytest.mark.asyncio
    async def test_generate_for_agent(self):
        """Test agent image generation"""
        from app.llm.image_generation import ImageGenerationService, GeneratedImage, ImageProvider

        service = ImageGenerationService()

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.provider = ImageProvider.OPENAI_DALLE
        mock_provider.generate = AsyncMock(return_value=[
            GeneratedImage(url="https://example.com/img.png")
        ])
        service.register_provider(mock_provider)

        result = await service.generate_for_agent(
            agent_name="TestBot",
            context="friendly assistant"
        )

        assert result.url == "https://example.com/img.png"

    @pytest.mark.asyncio
    async def test_generate_social_post_image(self):
        """Test social post image generation"""
        from app.llm.image_generation import ImageGenerationService, GeneratedImage, ImageProvider

        service = ImageGenerationService()

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.provider = ImageProvider.OPENAI_DALLE
        mock_provider.generate = AsyncMock(return_value=[
            GeneratedImage(url="https://example.com/post.png")
        ])
        service.register_provider(mock_provider)

        result = await service.generate_social_post_image(
            post_content="Check out this new DeFi protocol!",
            style="modern"
        )

        assert result.url == "https://example.com/post.png"

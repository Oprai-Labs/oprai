"""
Tests for FarCaster Client module.

Tests all FarCaster client operations.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestFarcasterReaction:
    """Test FarcasterReaction enum"""

    def test_reaction_values(self):
        """Test reaction enum values"""
        from app.clients.farcaster_client import FarcasterReaction

        assert FarcasterReaction.LIKE.value == "like"
        assert FarcasterReaction.RECAST.value == "recast"


class TestFarcasterUser:
    """Test FarcasterUser dataclass"""

    def test_user_creation(self):
        """Test creating FarcasterUser"""
        from app.clients.farcaster_client import FarcasterUser

        user = FarcasterUser(
            fid=123,
            username="testuser",
            display_name="Test User"
        )

        assert user.fid == 123
        assert user.username == "testuser"

    def test_user_defaults(self):
        """Test user default values"""
        from app.clients.farcaster_client import FarcasterUser

        user = FarcasterUser(fid=1, username="u", display_name="U")

        assert user.bio == ""
        assert user.follower_count == 0
        assert user.verified is False


class TestFarcasterCast:
    """Test FarcasterCast dataclass"""

    def test_cast_creation(self):
        """Test creating FarcasterCast"""
        from app.clients.farcaster_client import FarcasterCast

        cast = FarcasterCast(
            hash="abc123",
            fid=1,
            text="Hello world!",
            timestamp=datetime.now(timezone.utc)
        )

        assert cast.hash == "abc123"
        assert cast.text == "Hello world!"

    def test_cast_defaults(self):
        """Test cast default values"""
        from app.clients.farcaster_client import FarcasterCast

        cast = FarcasterCast(
            hash="h1",
            fid=1,
            text="Test",
            timestamp=datetime.now(timezone.utc)
        )

        assert cast.reactions == {}
        assert cast.replies == 0
        assert cast.embeds == []


class TestFarcasterMessage:
    """Test FarcasterMessage dataclass"""

    def test_message_creation(self):
        """Test creating FarcasterMessage"""
        from app.clients.farcaster_client import FarcasterMessage

        msg = FarcasterMessage(
            id="msg1",
            channel_id="channel1",
            user_id="123",
            user_name="user",
            content="Test",
            timestamp=datetime.now(timezone.utc)
        )

        assert msg.id == "msg1"
        assert msg.platform == "farcaster"


class TestFarcasterClientInit:
    """Test FarcasterClient initialization"""

    def test_init_with_credentials(self):
        """Test initialization with credentials"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(
            api_key="test_key",
            signer_uuid="signer123"
        )

        assert client.api_key == "test_key"
        assert client.signer_uuid == "signer123"
        assert client.base_url == "https://api.neynar.com/v2"

    def test_init_with_defaults(self):
        """Test initialization with defaults"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient()

        assert client.api_key is None
        assert client._running is False


class TestFarcasterClientHeaders:
    """Test header generation"""

    def test_get_headers(self):
        """Test _get_headers"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="test_key")
        headers = client._get_headers()

        assert "x-api-key" in headers
        assert headers["x-api-key"] == "test_key"


class TestFarcasterClientConnection:
    """Test connection state"""

    def test_is_connected_with_key(self):
        """Test is_connected when API key present"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")
        assert client.is_connected is True

    def test_is_connected_without_key(self):
        """Test is_connected when no API key"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient()
        assert client.is_connected is False


class TestFarcasterClientUser:
    """Test user operations"""

    @pytest.mark.asyncio
    async def test_get_user_success(self):
        """Test getting user by FID"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "user": {
                    "fid": 123,
                    "username": "testuser",
                    "display_name": "Test User",
                    "pfp_url": "https://example.com/pfp.png"
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="test")
            user = await client.get_user(123)

            assert user is not None
            assert user.username == "testuser"

    @pytest.mark.asyncio
    async def test_get_user_error(self):
        """Test getting user with error"""
        from app.clients.farcaster_client import FarcasterClient

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("API Error")

            client = FarcasterClient(api_key="test")
            user = await client.get_user(123)

            assert user is None

    @pytest.mark.asyncio
    async def test_get_user_by_username(self):
        """Test getting user by username"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "user": {"fid": 1, "username": "test", "display_name": "Test"}
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="test")
            user = await client.get_user_by_username("test")

            assert user is not None


class TestFarcasterClientCast:
    """Test cast operations"""

    @pytest.mark.asyncio
    async def test_post_cast_no_signer(self):
        """Test posting cast without signer"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")
        result = await client.post_cast("Test cast")

        assert result is None

    @pytest.mark.asyncio
    async def test_post_cast_success(self):
        """Test posting cast successfully"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "cast": {
                    "hash": "cast123",
                    "author": {"fid": 1, "username": "user", "display_name": "User"},
                    "text": "Test",
                    "timestamp": "2024-01-01T00:00:00Z"
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key", signer_uuid="signer")
            result = await client.post_cast("Test cast")

            assert result is not None
            assert result.text == "Test"

    @pytest.mark.asyncio
    async def test_get_cast_success(self):
        """Test getting cast"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "cast": {
                    "hash": "cast123",
                    "author": {"fid": 1, "username": "user", "display_name": "User"},
                    "text": "Test",
                    "timestamp": "2024-01-01T00:00:00Z"
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key")
            cast = await client.get_cast("cast123")

            assert cast is not None

    @pytest.mark.asyncio
    async def test_delete_cast_no_signer(self):
        """Test deleting cast without signer"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")
        result = await client.delete_cast("cast123")

        assert result is False


class TestFarcasterClientReactions:
    """Test reaction operations"""

    @pytest.mark.asyncio
    async def test_like_cast_no_signer(self):
        """Test liking cast without signer"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")
        result = await client.like_cast("cast123")

        assert result is False

    @pytest.mark.asyncio
    async def test_like_cast_success(self):
        """Test liking cast"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key", signer_uuid="signer")
            result = await client.like_cast("cast123")

            assert result is True

    @pytest.mark.asyncio
    async def test_recast_no_signer(self):
        """Test recasting without signer"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")
        result = await client.recast("cast123")

        assert result is False


class TestFarcasterClientFeed:
    """Test feed operations"""

    @pytest.mark.asyncio
    async def test_get_feed_success(self):
        """Test getting feed"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "casts": [
                    {"hash": "c1", "author": {"fid": 1, "username": "u", "display_name": "U"}, "text": "Test", "timestamp": "2024-01-01T00:00:00Z"}
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key")
            casts, cursor = await client.get_feed()

            assert len(casts) == 1
            assert cursor is None

    @pytest.mark.asyncio
    async def test_get_feed_error(self):
        """Test getting feed with error"""
        from app.clients.farcaster_client import FarcasterClient

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("Error")

            client = FarcasterClient(api_key="key")
            casts, cursor = await client.get_feed()

            assert casts == []
            assert cursor is None


class TestFarcasterClientFollow:
    """Test follow operations"""

    @pytest.mark.asyncio
    async def test_follow_no_signer(self):
        """Test following without signer"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")
        result = await client.follow_user(123)

        assert result is False

    @pytest.mark.asyncio
    async def test_follow_success(self):
        """Test following user"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key", signer_uuid="signer")
            result = await client.follow_user(123)

            assert result is True

    @pytest.mark.asyncio
    async def test_unfollow_success(self):
        """Test unfollowing user"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.delete = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key", signer_uuid="signer")
            result = await client.unfollow_user(123)

            assert result is True


class TestFarcasterClientChannel:
    """Test channel operations"""

    @pytest.mark.asyncio
    async def test_get_channel_success(self):
        """Test getting channel"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "channel": {"id": "defi", "name": "DeFi"}
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key")
            channel = await client.get_channel("defi")

            assert channel is not None
            assert channel["name"] == "DeFi"

    @pytest.mark.asyncio
    async def test_get_channel_feed_success(self):
        """Test getting channel feed"""
        from app.clients.farcaster_client import FarcasterClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {"casts": []}
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = FarcasterClient(api_key="key")
            casts = await client.get_channel_feed("defi")

            assert casts == []


class TestFarcasterClientListen:
    """Test listening operations"""

    def test_on_message(self):
        """Test registering message handler"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")

        async def handler(msg):
            pass

        client.on_message(handler)

        assert len(client._message_handlers) == 1

    @pytest.mark.asyncio
    async def test_stop(self):
        """Test stopping client"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient(api_key="key")
        client._running = True

        await client.stop()

        assert client._running is False


class TestFarcasterClientParse:
    """Test parsing helpers"""

    def test_parse_cast(self):
        """Test _parse_cast"""
        from app.clients.farcaster_client import FarcasterClient

        client = FarcasterClient()

        data = {
            "hash": "cast123",
            "author": {"fid": 1, "username": "user", "display_name": "User", "pfp_url": ""},
            "text": "Test cast",
            "timestamp": "2024-01-01T12:00:00Z"
        }

        cast = client._parse_cast(data)

        assert cast.hash == "cast123"
        assert cast.text == "Test cast"

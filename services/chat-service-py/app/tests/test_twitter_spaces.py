"""
Tests for Twitter Spaces Client module.

Tests TwitterSpacesClient and SpaceAutomationManager classes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


class TestSpaceState:
    """Test SpaceState enum"""

    def test_space_state_values(self):
        """Test SpaceState enum values"""
        from app.clients.twitter_spaces import SpaceState

        assert SpaceState.SCHEDULED.value == "scheduled"
        assert SpaceState.LIVE.value == "live"
        assert SpaceState.ENDED.value == "ended"


class TestSpaceInfo:
    """Test SpaceInfo dataclass"""

    def test_space_info_creation(self):
        """Test creating SpaceInfo"""
        from app.clients.twitter_spaces import SpaceInfo, SpaceState

        space = SpaceInfo(
            id="space123",
            title="Test Space",
            state=SpaceState.LIVE,
            host_id="host123",
            host_name="Test Host"
        )

        assert space.id == "space123"
        assert space.title == "Test Space"
        assert space.state == SpaceState.LIVE

    def test_space_info_defaults(self):
        """Test SpaceInfo default values"""
        from app.clients.twitter_spaces import SpaceInfo, SpaceState

        space = SpaceInfo(
            id="space123",
            title="Test",
            state=SpaceState.LIVE,
            host_id="host123",
            host_name="Host"
        )

        assert space.participant_count == 0
        assert space.duration_minutes == 0
        assert space.is_ticketed is False
        assert space.lang == "en"


class TestSpaceParticipant:
    """Test SpaceParticipant dataclass"""

    def test_space_participant_creation(self):
        """Test creating SpaceParticipant"""
        from app.clients.twitter_spaces import SpaceParticipant

        participant = SpaceParticipant(
            user_id="user123",
            username="testuser",
            display_name="Test User"
        )

        assert participant.user_id == "user123"
        assert participant.is_speaker is False
        assert participant.is_muted is True


class TestSpaceMessage:
    """Test SpaceMessage dataclass"""

    def test_space_message_creation(self):
        """Test creating SpaceMessage"""
        from app.clients.twitter_spaces import SpaceMessage
        from datetime import timezone

        msg = SpaceMessage(
            id="msg123",
            space_id="space123",
            user_id="user123",
            content="Hello!",
            timestamp=datetime.now(timezone.utc)
        )

        assert msg.id == "msg123"
        assert msg.is_transcription is True


class TestTwitterSpacesClientInit:
    """Test TwitterSpacesClient initialization"""

    def test_init_with_credentials(self):
        """Test initialization with credentials"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        client = TwitterSpacesClient(
            bearer_token="test_token",
            api_key="api_key",
            api_secret="api_secret"
        )

        assert client.bearer_token == "test_token"
        assert client.api_key == "api_key"
        assert client.base_url == "https://api.twitter.com/2"

    def test_init_with_defaults(self):
        """Test initialization with defaults"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        client = TwitterSpacesClient()

        assert client.bearer_token is None
        assert client._active_spaces == {}


class TestTwitterSpacesClientHeaders:
    """Test header generation"""

    def test_get_headers(self):
        """Test _get_headers returns correct format"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        client = TwitterSpacesClient(bearer_token="test_token")
        headers = client._get_headers()

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test_token"
        assert headers["Content-Type"] == "application/json"


class TestTwitterSpacesClientSearch:
    """Test search functionality"""

    @pytest.mark.asyncio
    async def test_search_spaces_success(self):
        """Test successful space search"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceState

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "space1",
                    "title": "DeFi Space",
                    "state": "live",
                    "host_ids": ["host1"],
                    "participant_count": 150
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = TwitterSpacesClient(bearer_token="test")
            spaces = await client.search_spaces("DeFi")

            assert len(spaces) == 1
            assert spaces[0].title == "DeFi Space"

    @pytest.mark.asyncio
    async def test_search_spaces_empty(self):
        """Test search with no results"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = TwitterSpacesClient(bearer_token="test")
            spaces = await client.search_spaces("nonexistent")

            assert spaces == []

    @pytest.mark.asyncio
    async def test_search_spaces_error(self):
        """Test search handles errors"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("API Error")

            client = TwitterSpacesClient(bearer_token="test")
            spaces = await client.search_spaces("test")

            assert spaces == []


class TestTwitterSpacesClientGetSpace:
    """Test get_space functionality"""

    @pytest.mark.asyncio
    async def test_get_space_success(self):
        """Test getting space details"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "id": "space1",
                "title": "Test Space",
                "state": "live",
                "host_ids": ["host1"],
                "participant_count": 100
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = TwitterSpacesClient(bearer_token="test")
            space = await client.get_space("space1")

            assert space is not None
            assert space.title == "Test Space"

    @pytest.mark.asyncio
    async def test_get_space_not_found(self):
        """Test getting non-existent space"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            client = TwitterSpacesClient(bearer_token="test")
            space = await client.get_space("nonexistent")

            assert space is None


class TestTwitterSpacesClientDiscover:
    """Test discover_trending_spaces"""

    @pytest.mark.asyncio
    async def test_discover_trending_spaces(self):
        """Test discovering trending spaces"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceInfo, SpaceState

        client = TwitterSpacesClient(bearer_token="test")

        with patch.object(client, "search_spaces", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [
                SpaceInfo(id="s1", title="Space 1", state=SpaceState.LIVE, host_id="h1", host_name="Host", participant_count=100),
                SpaceInfo(id="s2", title="Space 2", state=SpaceState.LIVE, host_id="h2", host_name="Host2", participant_count=200),
            ]

            spaces = await client.discover_trending_spaces(["DeFi", "Solana"])

            assert len(spaces) == 2


class TestTwitterSpacesClientJoin:
    """Test join_space_simulation"""

    @pytest.mark.asyncio
    async def test_join_space_not_found(self):
        """Test joining non-existent space"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        client = TwitterSpacesClient(bearer_token="test")

        with patch.object(client, "get_space", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await client.join_space_simulation("space123", "Agent")

            assert result["success"] is False
            assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_join_space_not_live(self):
        """Test joining non-live space"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceInfo, SpaceState

        client = TwitterSpacesClient(bearer_token="test")

        with patch.object(client, "get_space", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = SpaceInfo(
                id="space123",
                title="Scheduled Space",
                state=SpaceState.SCHEDULED,
                host_id="host1",
                host_name="Host"
            )

            result = await client.join_space_simulation("space123", "Agent")

            assert result["success"] is False
            assert "SCHEDULED" in result["error"]

    @pytest.mark.asyncio
    async def test_join_space_success(self):
        """Test successful join simulation"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceInfo, SpaceState

        client = TwitterSpacesClient(bearer_token="test")

        with patch.object(client, "get_space", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = SpaceInfo(
                id="space123",
                title="Live Space",
                state=SpaceState.LIVE,
                host_id="host1",
                host_name="Host",
                participant_count=50
            )

            result = await client.join_space_simulation("space123", "Agent")

            assert result["success"] is True
            assert result["space_title"] == "Live Space"


class TestTwitterSpacesClientEvents:
    """Test event handling"""

    def test_on_space_event(self):
        """Test registering space event handler"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        client = TwitterSpacesClient(bearer_token="test")

        def handler(space):
            pass

        client.on_space_event("space123", handler)

        assert "space123" in client._space_handlers

    @pytest.mark.asyncio
    async def test_stop_monitoring(self):
        """Test stopping space monitoring"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        client = TwitterSpacesClient(bearer_token="test")
        client._active_spaces["space123"] = MagicMock()

        await client.stop_monitoring("space123")

        assert "space123" not in client._active_spaces


class TestDateParsing:
    """Test date parsing"""

    def test_parse_valid_date(self):
        """Test parsing valid date"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        result = TwitterSpacesClient._parse_date("2024-01-01T12:00:00Z")

        assert result is not None

    def test_parse_none_date(self):
        """Test parsing None date"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        result = TwitterSpacesClient._parse_date(None)

        assert result is None

    def test_parse_invalid_date(self):
        """Test parsing invalid date"""
        from app.clients.twitter_spaces import TwitterSpacesClient

        result = TwitterSpacesClient._parse_date("invalid-date")

        assert result is None


class TestSpaceAutomationManager:
    """Test SpaceAutomationManager"""

    def test_init(self):
        """Test automation manager initialization"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceAutomationManager

        client = TwitterSpacesClient(bearer_token="test")
        manager = SpaceAutomationManager(client)

        assert manager.client is not None
        assert manager._monitored_topics == {}
        assert manager._auto_join_rules == []

    def test_add_auto_join_rule(self):
        """Test adding auto-join rule"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceAutomationManager

        client = TwitterSpacesClient(bearer_token="test")
        manager = SpaceAutomationManager(client)

        manager.add_auto_join_rule(
            topic="DeFi",
            character_id="char123",
            min_participants=100,
            keywords=["Solana", "yield"]
        )

        assert len(manager._auto_join_rules) == 1
        rule = manager._auto_join_rules[0]
        assert rule["topic"] == "DeFi"
        assert rule["min_participants"] == 100

    def test_matches_rule_disabled(self):
        """Test rule matching when disabled"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceAutomationManager, SpaceInfo, SpaceState

        client = TwitterSpacesClient(bearer_token="test")
        manager = SpaceAutomationManager(client)

        rule = {"enabled": False, "min_participants": 100, "keywords": []}
        space = SpaceInfo(id="s1", title="Test", state=SpaceState.LIVE, host_id="h1", host_name="H")

        assert manager._matches_rule(space, rule) is False

    def test_matches_rule_participants(self):
        """Test rule matching by participant count"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceAutomationManager, SpaceInfo, SpaceState

        client = TwitterSpacesClient(bearer_token="test")
        manager = SpaceAutomationManager(client)

        rule = {"enabled": True, "min_participants": 100, "keywords": []}
        space = SpaceInfo(id="s1", title="Test", state=SpaceState.LIVE, host_id="h1", host_name="H", participant_count=50)

        assert manager._matches_rule(space, rule) is False

    def test_matches_rule_keywords(self):
        """Test rule matching by keywords"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceAutomationManager, SpaceInfo, SpaceState

        client = TwitterSpacesClient(bearer_token="test")
        manager = SpaceAutomationManager(client)

        rule = {"enabled": True, "min_participants": 10, "keywords": ["Solana", "DeFi"]}
        space = SpaceInfo(id="s1", title="DeFi on Solana Talk", state=SpaceState.LIVE, host_id="h1", host_name="H", participant_count=100)

        assert manager._matches_rule(space, rule) is True

    def test_stop_all_monitoring(self):
        """Test stopping all monitoring"""
        from app.clients.twitter_spaces import TwitterSpacesClient, SpaceAutomationManager

        client = TwitterSpacesClient(bearer_token="test")
        manager = SpaceAutomationManager(client)

        # Create mock tasks
        mock_task = MagicMock()
        manager._monitored_topics = {"DeFi": mock_task, "Solana": mock_task}

        manager.stop_all_monitoring()

        assert len(manager._monitored_topics) == 0

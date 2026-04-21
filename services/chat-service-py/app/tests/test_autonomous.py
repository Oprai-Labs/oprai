"""
Tests for Autonomous Agent module.

Tests autonomous loop and manager classes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta


class TestActionType:
    """Test ActionType enum"""

    def test_action_type_values(self):
        """Test all action type values"""
        from app.agents.autonomous import ActionType

        assert ActionType.POST_TWEET.value == "post_tweet"
        assert ActionType.CHECK_PRICES.value == "check_prices"
        assert ActionType.ANALYZE_PORTFOLIO.value == "analyze_portfolio"
        assert ActionType.SCAN_FOR_OPPORTUNITIES.value == "scan_opportunities"
        assert ActionType.ENGAGE_SOCIAL.value == "engage_social"


class TestScheduledAction:
    """Test ScheduledAction dataclass"""

    def test_scheduled_action_defaults(self):
        """Test default values"""
        from app.agents.autonomous import ScheduledAction, ActionType

        action = ScheduledAction(
            action_type=ActionType.CHECK_PRICES,
            interval_minutes=5
        )

        assert action.enabled is True
        assert action.last_run is None
        assert action.next_run is None
        assert action.callback is None
        assert action.conditions == []

    def test_scheduled_action_with_values(self):
        """Test with custom values"""
        from app.agents.autonomous import ScheduledAction, ActionType

        def callback():
            pass

        action = ScheduledAction(
            action_type=ActionType.POST_TWEET,
            interval_minutes=60,
            callback=callback,
            conditions=[{"has_twitter": True}]
        )

        assert action.callback is callback
        assert len(action.conditions) == 1


class TestActionResult:
    """Test ActionResult dataclass"""

    def test_action_result_defaults(self):
        """Test default values"""
        from app.agents.autonomous import ActionResult, ActionType

        result = ActionResult(
            action_type=ActionType.CHECK_PRICES,
            success=True
        )

        assert result.result is None
        assert result.error is None
        assert result.timestamp is not None

    def test_action_result_with_data(self):
        """Test with data"""
        from app.agents.autonomous import ActionResult, ActionType

        result = ActionResult(
            action_type=ActionType.CHECK_PRICES,
            success=True,
            result={"prices": [1, 2, 3]},
            error=None
        )

        assert result.result == {"prices": [1, 2, 3]}


class TestAutonomousLoopInit:
    """Test AutonomousLoop initialization"""

    def test_init(self):
        """Test loop initialization"""
        from app.agents.autonomous import AutonomousLoop

        mock_agent = MagicMock()
        mock_agent.character.name = "TestAgent"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        assert loop.agent is not None
        assert loop._running is False
        assert loop._scheduled_actions != {}

    def test_default_actions_setup(self):
        """Test default actions are set up"""
        from app.agents.autonomous import AutonomousLoop, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        assert ActionType.CHECK_PRICES in loop._scheduled_actions
        assert ActionType.SCAN_FOR_OPPORTUNITIES in loop._scheduled_actions
        assert ActionType.CHECK_GOALS in loop._scheduled_actions


class TestAutonomousLoopScheduling:
    """Test scheduling methods"""

    def test_schedule_action(self):
        """Test scheduling an action"""
        from app.agents.autonomous import AutonomousLoop, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        def custom_callback():
            pass

        loop.schedule_action(
            action_type=ActionType.ANALYZE_PORTFOLIO,
            interval_minutes=30,
            callback=custom_callback,
            conditions=[{"business_hours_only": True}]
        )

        assert ActionType.ANALYZE_PORTFOLIO in loop._scheduled_actions
        action = loop._scheduled_actions[ActionType.ANALYZE_PORTFOLIO]
        assert action.interval_minutes == 30
        assert action.callback is custom_callback

    def test_remove_action(self):
        """Test removing an action"""
        from app.agents.autonomous import AutonomousLoop, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)
        loop.remove_action(ActionType.POST_TWEET)

        assert ActionType.POST_TWEET not in loop._scheduled_actions

    def test_enable_action(self):
        """Test enabling an action"""
        from app.agents.autonomous import AutonomousLoop, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)
        loop.disable_action(ActionType.CHECK_PRICES)
        assert loop._scheduled_actions[ActionType.CHECK_PRICES].enabled is False

        loop.enable_action(ActionType.CHECK_PRICES)
        assert loop._scheduled_actions[ActionType.CHECK_PRICES].enabled is True

    def test_disable_action(self):
        """Test disabling an action"""
        from app.agents.autonomous import AutonomousLoop, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)
        loop.disable_action(ActionType.CHECK_PRICES)

        assert loop._scheduled_actions[ActionType.CHECK_PRICES].enabled is False


class TestAutonomousLoopRun:
    """Test running the loop"""

    @pytest.mark.asyncio
    async def test_start(self):
        """Test starting the loop"""
        from app.agents.autonomous import AutonomousLoop

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)
        await loop.start()

        assert loop._running is True

        await loop.stop()

    @pytest.mark.asyncio
    async def test_stop(self):
        """Test stopping the loop"""
        from app.agents.autonomous import AutonomousLoop

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)
        loop._running = True

        await loop.stop()

        assert loop._running is False


class TestAutonomousLoopConditions:
    """Test condition checking"""

    @pytest.mark.asyncio
    async def test_check_conditions_no_conditions(self):
        """Test with no conditions"""
        from app.agents.autonomous import AutonomousLoop, ScheduledAction, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        action = ScheduledAction(action_type=ActionType.CHECK_PRICES, interval_minutes=5)
        result = await loop._check_conditions(action)

        assert result is True

    @pytest.mark.asyncio
    async def test_check_conditions_twitter(self):
        """Test twitter condition"""
        from app.agents.autonomous import AutonomousLoop, ScheduledAction, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = ["twitter"]
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        action = ScheduledAction(
            action_type=ActionType.POST_TWEET,
            interval_minutes=60,
            conditions=[{"has_twitter": True}]
        )

        result = await loop._check_conditions(action)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_conditions_twitter_missing(self):
        """Test twitter condition when missing"""
        from app.agents.autonomous import AutonomousLoop, ScheduledAction, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        action = ScheduledAction(
            action_type=ActionType.POST_TWEET,
            interval_minutes=60,
            conditions=[{"has_twitter": True}]
        )

        result = await loop._check_conditions(action)
        assert result is False


class TestAutonomousLoopExecute:
    """Test action execution"""

    @pytest.mark.asyncio
    async def test_execute_action_with_callback(self):
        """Test executing action with callback"""
        from app.agents.autonomous import AutonomousLoop, ScheduledAction, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        async def my_callback():
            return {"done": True}

        action = ScheduledAction(
            action_type=ActionType.CHECK_PRICES,
            interval_minutes=5,
            callback=my_callback
        )

        result = await loop._execute_action(action)

        assert result.success is True
        assert result.result == {"done": True}

    @pytest.mark.asyncio
    async def test_execute_action_no_callback(self):
        """Test executing action without callback"""
        from app.agents.autonomous import AutonomousLoop, ScheduledAction, ActionType

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        action = ScheduledAction(
            action_type=ActionType.CHECK_PRICES,
            interval_minutes=5
        )

        result = await loop._execute_action(action)

        assert result.success is False
        assert "No callback" in result.error


class TestAutonomousLoopStatus:
    """Test status reporting"""

    def test_get_status(self):
        """Test getting status"""
        from app.agents.autonomous import AutonomousLoop

        mock_agent = MagicMock()
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = AutonomousLoop(mock_agent)

        status = loop.get_status()

        assert "running" in status
        assert "scheduled_actions" in status
        assert "recent_actions" in status


class TestAutonomousManager:
    """Test AutonomousManager"""

    def test_init(self):
        """Test manager initialization"""
        from app.agents.autonomous import AutonomousManager

        manager = AutonomousManager()

        assert manager._loops == {}

    def test_register_agent(self):
        """Test registering an agent"""
        from app.agents.autonomous import AutonomousManager

        manager = AutonomousManager()

        mock_agent = MagicMock()
        mock_agent.agent_id = "agent_123"
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        loop = manager.register_agent(mock_agent)

        assert "agent_123" in manager._loops
        assert loop is not None

    def test_get_loop(self):
        """Test getting agent loop"""
        from app.agents.autonomous import AutonomousManager

        manager = AutonomousManager()

        mock_agent = MagicMock()
        mock_agent.agent_id = "agent_456"
        mock_agent.character.name = "Test"
        mock_agent.character.clients = []
        mock_agent._context.goals = []

        manager.register_agent(mock_agent)
        loop = manager.get_loop("agent_456")

        assert loop is not None

    def test_get_loop_not_found(self):
        """Test getting non-existent loop"""
        from app.agents.autonomous import AutonomousManager

        manager = AutonomousManager()
        loop = manager.get_loop("nonexistent")

        assert loop is None


class TestGlobalAutonomousManager:
    """Test global manager"""

    def test_get_autonomous_manager(self):
        """Test getting manager"""
        from app.agents.autonomous import get_autonomous_manager

        # Reset global
        import app.agents.autonomous as aut_module
        aut_module._autonomous_manager = None

        manager = get_autonomous_manager()

        assert manager is not None

    def test_singleton(self):
        """Test singleton behavior"""
        from app.agents.autonomous import get_autonomous_manager

        # Reset global
        import app.agents.autonomous as aut_module
        aut_module._autonomous_manager = None

        manager1 = get_autonomous_manager()
        manager2 = get_autonomous_manager()

        assert manager1 is manager2

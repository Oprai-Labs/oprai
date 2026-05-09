"""
Autonomous Agent Loop

Enables agents to take actions without user prompts.
Based on elizaOS autonomous capabilities.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    """Types of autonomous actions"""
    POST_TWEET = "post_tweet"
    CHECK_PRICES = "check_prices"
    ANALYZE_PORTFOLIO = "analyze_portfolio"
    SCAN_FOR_OPPORTUNITIES = "scan_opportunities"
    ENGAGE_SOCIAL = "engage_social"
    SUMMARIZE_NEWS = "summarize_news"
    CHECK_GOALS = "check_goals"
    CLEANUP_MEMORY = "cleanup_memory"


@dataclass
class ScheduledAction:
    """A scheduled autonomous action"""
    action_type: ActionType
    interval_minutes: int
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    enabled: bool = True
    callback: Optional[Callable] = None
    conditions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ActionResult:
    """Result of an autonomous action"""
    action_type: ActionType
    success: bool
    result: Any = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


class AutonomousLoop:
    """
    Manages autonomous agent actions.

    Agents can perform actions without user input:
    - Scheduled posts
    - Market monitoring
    - Opportunity scanning
    - Social engagement
    """

    def __init__(self, agent_runtime: Any):
        self.agent = agent_runtime
        self._scheduled_actions: dict[ActionType, ScheduledAction] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._action_history: list[ActionResult] = []

        # Default actions
        self._setup_defaults()

    def _setup_defaults(self):
        """Setup default autonomous actions"""
        # Check prices every 5 minutes
        self.schedule_action(
            ActionType.CHECK_PRICES,
            interval_minutes=5,
            callback=self._check_prices,
        )

        # Scan for opportunities every 15 minutes
        self.schedule_action(
            ActionType.SCAN_FOR_OPPORTUNITIES,
            interval_minutes=15,
            callback=self._scan_opportunities,
        )

        # Check goals every 30 minutes
        self.schedule_action(
            ActionType.CHECK_GOALS,
            interval_minutes=30,
            callback=self._check_goals,
        )

        # Post to social (if configured) every 2-4 hours
        self.schedule_action(
            ActionType.POST_TWEET,
            interval_minutes=random.randint(120, 240),
            callback=self._post_content,
            conditions=[{"has_twitter": True}],
        )

        # Engage with social mentions every 10 minutes
        self.schedule_action(
            ActionType.ENGAGE_SOCIAL,
            interval_minutes=10,
            callback=self._engage_social,
        )

    def schedule_action(
        self,
        action_type: ActionType,
        interval_minutes: int,
        callback: Optional[Callable] = None,
        conditions: Optional[list[dict]] = None,
        start_delay_minutes: int = 0,
    ) -> None:
        """Schedule an autonomous action"""
        next_run = datetime.utcnow() + timedelta(minutes=start_delay_minutes)

        self._scheduled_actions[action_type] = ScheduledAction(
            action_type=action_type,
            interval_minutes=interval_minutes,
            next_run=next_run,
            callback=callback,
            conditions=conditions or [],
        )

        logger.info(f"Scheduled action: {action_type.value} every {interval_minutes}min")

    def remove_action(self, action_type: ActionType) -> None:
        """Remove a scheduled action"""
        self._scheduled_actions.pop(action_type, None)

    def enable_action(self, action_type: ActionType) -> None:
        """Enable a scheduled action"""
        if action_type in self._scheduled_actions:
            self._scheduled_actions[action_type].enabled = True

    def disable_action(self, action_type: ActionType) -> None:
        """Disable a scheduled action"""
        if action_type in self._scheduled_actions:
            self._scheduled_actions[action_type].enabled = False

    async def start(self) -> None:
        """Start the autonomous loop"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Autonomous loop started for agent: {self.agent.character.name}")

    async def stop(self) -> None:
        """Stop the autonomous loop"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Autonomous loop stopped")

    async def _run_loop(self) -> None:
        """Main loop for autonomous actions"""
        while self._running:
            try:
                now = datetime.utcnow()

                for action_type, action in self._scheduled_actions.items():
                    if not action.enabled:
                        continue

                    if action.next_run and now >= action.next_run:
                        # Check conditions
                        if await self._check_conditions(action):
                            # Execute action
                            result = await self._execute_action(action)

                            # Update schedule
                            action.last_run = now
                            action.next_run = now + timedelta(minutes=action.interval_minutes)

                            # Store result
                            self._action_history.append(result)

                            # Keep only last 100 results
                            if len(self._action_history) > 100:
                                self._action_history = self._action_history[-100:]

                # Sleep for a bit
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Autonomous loop error", exc_info=True)
                await asyncio.sleep(60)

    async def _check_conditions(self, action: ScheduledAction) -> bool:
        """Check if action conditions are met"""
        for condition in action.conditions:
            # Check platform availability
            if "has_twitter" in condition:
                has_twitter = "twitter" in self.agent.character.clients
                if condition["has_twitter"] and not has_twitter:
                    return False

            if "has_discord" in condition:
                has_discord = "discord" in self.agent.character.clients
                if condition["has_discord"] and not has_discord:
                    return False

            # Check time conditions
            if "business_hours_only" in condition:
                hour = datetime.utcnow().hour
                if hour < 9 or hour > 17:
                    return False

        return True

    async def _execute_action(self, action: ScheduledAction) -> ActionResult:
        """Execute a scheduled action"""
        try:
            if action.callback:
                result = await action.callback()
                return ActionResult(
                    action_type=action.action_type,
                    success=True,
                    result=result,
                )
            else:
                return ActionResult(
                    action_type=action.action_type,
                    success=False,
                    error="No callback defined",
                )
        except Exception as e:
            logger.error("Action {action.action_type} failed", exc_info=True)
            return ActionResult(
                action_type=action.action_type,
                success=False,
                error=str(e),
            )

    # ==================== Default Action Callbacks ====================

    async def _check_prices(self) -> dict:
        """Check token prices"""
        # Would call price provider
        logger.info("Checking prices...")
        return {"checked": True, "timestamp": datetime.utcnow().isoformat()}

    async def _scan_opportunities(self) -> dict:
        """Scan for DeFi opportunities"""
        logger.info("Scanning for opportunities...")

        # Check for:
        # - High APY pools
        # - Arbitrage opportunities
        # - New token launches
        # - Unusual market activity

        return {"opportunities_found": 0}

    async def _check_goals(self) -> dict:
        """Check progress on agent goals"""
        goals = self.agent._context.goals
        logger.info(f"Checking {len(goals)} goals...")

        # Evaluate goal progress
        progress = {}
        for goal in goals:
            # Would use LLM to evaluate progress
            progress[goal] = "in_progress"

        return {"goals_checked": len(goals), "progress": progress}

    async def _post_content(self) -> dict:
        """Post content to social media"""
        from app.llm import get_llm_service
        from app.services.character import PromptBuilder

        llm = get_llm_service()
        builder = PromptBuilder(self.agent.character)

        # Generate post prompt
        topics = self.agent.character.topics or ["DeFi"]
        topic = random.choice(topics)
        adjectives = self.agent.character.adjectives or ["engaging"]
        adjective = random.choice(adjectives)

        prompt = builder.build_twitter_post_prompt(
            topic=topic,
            adjective=adjective,
        )

        # Generate post
        response = await llm.acomplete([
            {"role": "user", "content": prompt}
        ])

        logger.info(f"Generated post: {response[:100]}...")

        # Would post to Twitter here
        return {"post_generated": response}

    async def _engage_social(self) -> dict:
        """Engage with social mentions"""
        # Would check for:
        # - New mentions
        # - Comments on posts
        # - Direct messages

        logger.info("Checking for social engagement...")
        return {"engagements": 0}

    def get_status(self) -> dict:
        """Get autonomous loop status"""
        return {
            "running": self._running,
            "scheduled_actions": {
                action_type.value: {
                    "interval_minutes": action.interval_minutes,
                    "enabled": action.enabled,
                    "last_run": action.last_run.isoformat() if action.last_run else None,
                    "next_run": action.next_run.isoformat() if action.next_run else None,
                }
                for action_type, action in self._scheduled_actions.items()
            },
            "recent_actions": [
                {
                    "action": r.action_type.value,
                    "success": r.success,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in self._action_history[-10:]
            ],
        }


class AutonomousManager:
    """
    Manages autonomous loops for multiple agents.
    """

    def __init__(self):
        self._loops: dict[str, AutonomousLoop] = {}

    def register_agent(self, agent_runtime: Any) -> AutonomousLoop:
        """Register an agent for autonomous actions"""
        agent_id = agent_runtime.agent_id

        if agent_id not in self._loops:
            self._loops[agent_id] = AutonomousLoop(agent_runtime)

        return self._loops[agent_id]

    def get_loop(self, agent_id: str) -> Optional[AutonomousLoop]:
        """Get autonomous loop for an agent"""
        return self._loops.get(agent_id)

    async def start_all(self) -> None:
        """Start all autonomous loops"""
        for loop in self._loops.values():
            await loop.start()

    async def stop_all(self) -> None:
        """Stop all autonomous loops"""
        for loop in self._loops.values():
            await loop.stop()

    def get_status(self) -> dict:
        """Get status of all autonomous loops"""
        return {
            "agents": {
                agent_id: loop.get_status()
                for agent_id, loop in self._loops.items()
            }
        }


# Global autonomous manager
_autonomous_manager: Optional[AutonomousManager] = None


def get_autonomous_manager() -> AutonomousManager:
    """Get the global autonomous manager"""
    global _autonomous_manager
    if _autonomous_manager is None:
        _autonomous_manager = AutonomousManager()
    return _autonomous_manager

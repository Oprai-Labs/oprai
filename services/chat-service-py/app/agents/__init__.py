"""
Multi-Agent Architecture

Based on elizaOS multi-agent runtime.
Manages multiple AI agents with different personalities running simultaneously.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from collections.abc import Callable
from typing import Any, Optional

from app.clients import ClientManager, PlatformMessage
from app.models.character import Character
from app.services.character import PromptBuilder
from app.services.character.loader import CharacterLoader

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class AgentContext:
    """Runtime context for an agent"""
    agent_id: str
    character: Character
    status: AgentStatus = AgentStatus.IDLE
    message_count: int = 0
    last_activity: datetime | None = None
    error_message: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentMessage:
    """Message processed by an agent"""
    id: str
    agent_id: str
    role: str  # user, assistant, system
    content: str
    timestamp: datetime
    platform: str | None = None
    channel_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """
    Runtime for a single AI agent.
    Handles message processing, state management, and LLM interaction.
    """

    def __init__(
        self,
        character: Character,
        llm_service: Any = None,
        memory_service: Any = None,
    ):
        self.character = character
        self.agent_id = character.id or str(uuid.uuid4())
        self.llm_service = llm_service
        self.memory_service = memory_service
        self.prompt_builder = PromptBuilder(character)

        self._context = AgentContext(
            agent_id=self.agent_id,
            character=character,
        )
        self._conversation_history: list[dict[str, str]] = []
        self._lock = asyncio.Lock()

    @property
    def status(self) -> AgentStatus:
        return self._context.status

    @property
    def is_running(self) -> bool:
        return self._context.status == AgentStatus.RUNNING

    async def start(self) -> None:
        """Start the agent runtime"""
        self._context.status = AgentStatus.RUNNING
        self._context.last_activity = datetime.utcnow()
        logger.info(f"Agent {self.character.name} started")

    async def stop(self) -> None:
        """Stop the agent runtime"""
        self._context.status = AgentStatus.IDLE
        logger.info(f"Agent {self.character.name} stopped")

    async def pause(self) -> None:
        """Pause the agent"""
        self._context.status = AgentStatus.PAUSED

    async def resume(self) -> None:
        """Resume the agent"""
        self._context.status = AgentStatus.RUNNING

    async def process_message(
        self,
        message: str,
        user_id: str | None = None,
        platform: str | None = None,
        channel_id: str | None = None,
    ) -> str:
        """
        Process an incoming message and generate a response.

        Args:
            message: User message
            user_id: User identifier
            platform: Platform type (discord, telegram, etc.)
            channel_id: Channel/conversation ID

        Returns:
            Agent response
        """
        async with self._lock:
            if not self.is_running:
                return ""

            try:
                # Build context
                context = await self._build_context(user_id)

                # Add user message to history
                self._conversation_history.append({
                    "role": "user",
                    "content": message,
                })

                # Build prompt with character
                system_prompt = self.prompt_builder.build_system_prompt()

                # Prepare messages for LLM
                messages = [
                    {"role": "system", "content": system_prompt},
                    *self._conversation_history[-20:],  # Keep last 20 messages
                ]

                # Call LLM
                response = await self._call_llm(messages)

                # Add assistant response to history
                self._conversation_history.append({
                    "role": "assistant",
                    "content": response,
                })

                # Update context
                self._context.message_count += 1
                self._context.last_activity = datetime.utcnow()

                # Extract facts and goals
                await self._extract_facts(message, response)

                return response

            except Exception as e:
                logger.error("Agent {self.character.name} error", exc_info=True)
                self._context.status = AgentStatus.ERROR
                self._context.error_message = str(e)
                raise

    async def _build_context(self, user_id: str | None = None) -> str:
        """Build context string for the agent"""
        parts = []

        # Add goals
        if self._context.goals:
            parts.append("Current Goals:\n" + "\n".join(f"- {g}" for g in self._context.goals))

        # Add facts about user
        if user_id:
            user_facts = [f for f in self._context.facts if f.get("user_id") == user_id]
            if user_facts:
                parts.append("User Facts:\n" + "\n".join(f"- {f['key']}: {f['value']}" for f in user_facts))

        return "\n\n".join(parts)

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call the LLM service"""
        if self.llm_service:
            return await self.llm_service.acomplete(messages)

        # Fallback to direct OpenAI call
        from openai import AsyncOpenAI

        from app.config import settings

        client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)
        model = self.character.settings.model if self.character.settings else "gpt-4o-mini"

        response = await client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=messages,
            max_tokens=2000,
            temperature=0.7,
        )

        return response.choices[0].message.content or ""

    async def _extract_facts(self, user_message: str, agent_response: str) -> None:
        """Extract facts from conversation using LLM"""
        try:
            import openai
            client = openai.AsyncOpenAI()

            from app.utils.prompt_safety import sanitize_user_input_for_prompt
            safe_user = sanitize_user_input_for_prompt(user_message)
            safe_agent = sanitize_user_input_for_prompt(agent_response)
            prompt = f"""Extract key facts from this conversation. Return as JSON array of {{"key": "...", "value": "..."}}.

User: {safe_user}
Assistant: {safe_agent}

Facts (JSON array only):"""

            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.3,
            )

            content = response.choices[0].message.content or "[]"
            # Parse JSON from response
            import json
            facts = json.loads(content.strip().strip("```json").strip("```"))
            for fact in facts:
                if isinstance(fact, dict) and "key" in fact and "value" in fact:
                    self.add_fact(fact["key"], fact["value"])

        except Exception as e:
            # Non-critical: just log and continue
            pass

    def add_goal(self, goal: str) -> None:
        """Add a goal for the agent"""
        self._context.goals.append(goal)

    def remove_goal(self, goal: str) -> None:
        """Remove a goal"""
        if goal in self._context.goals:
            self._context.goals.remove(goal)

    def add_fact(self, key: str, value: Any, user_id: str | None = None) -> None:
        """Add a fact about a user or context"""
        self._context.facts.append({
            "key": key,
            "value": value,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def get_context(self) -> AgentContext:
        """Get agent context"""
        return self._context

    def clear_history(self) -> None:
        """Clear conversation history"""
        self._conversation_history.clear()


class AgentGroup:
    """
    Group of agents that can collaborate.
    Agents in a group can share context and delegate tasks.
    """

    def __init__(self, name: str):
        self.name = name
        self.group_id = str(uuid.uuid4())
        self._agents: dict[str, AgentRuntime] = {}
        self._shared_context: dict[str, Any] = {}

    def add_agent(self, agent: AgentRuntime) -> None:
        """Add an agent to the group"""
        self._agents[agent.agent_id] = agent
        logger.info(f"Added agent {agent.character.name} to group {self.name}")

    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent from the group"""
        self._agents.pop(agent_id, None)

    def get_agent(self, agent_id: str) -> AgentRuntime | None:
        """Get an agent by ID"""
        return self._agents.get(agent_id)

    def get_agents(self) -> list[AgentRuntime]:
        """Get all agents in the group"""
        return list(self._agents.values())

    def set_shared_context(self, key: str, value: Any) -> None:
        """Set shared context value"""
        self._shared_context[key] = value

    def get_shared_context(self, key: str, default: Any = None) -> Any:
        """Get shared context value"""
        return self._shared_context.get(key, default)

    async def broadcast_message(self, message: str) -> dict[str, str]:
        """Broadcast a message to all agents"""
        results = {}
        for agent_id, agent in self._agents.items():
            if agent.is_running:
                try:
                    response = await agent.process_message(message)
                    results[agent_id] = response
                except Exception as e:
                    logger.error("Broadcast to {agent_id} failed", exc_info=True)
        return results

    async def delegate_task(
        self,
        task: str,
        agent_filter: Callable[..., Any] | None = None,
    ) -> str | None:
        """Delegate a task to the most suitable agent"""
        candidates = self._agents.values()
        if agent_filter:
            candidates = [a for a in candidates if agent_filter(a)]

        if not candidates:
            return None

        # Smart delegation: score agents by capability match
        scored_agents = []
        task_lower = task.lower()

        for agent in candidates:
            score = 0
            character = agent.character

            # Check topic relevance
            if character.topics:
                for topic in character.topics:
                    if topic.lower() in task_lower:
                        score += 10

            # Check expertise areas
            if hasattr(character, 'expertise') and character.expertise:
                for exp in character.expertise:
                    if exp.lower() in task_lower:
                        score += 15

            # Prefer running agents for continuity
            if agent.is_running:
                score += 5

            scored_agents.append((score, agent))

        # Sort by score descending
        scored_agents.sort(key=lambda x: x[0], reverse=True)

        # Delegate to best match
        if scored_agents and scored_agents[0][1].is_running:
            return await scored_agents[0][1].process_message(task)

        return None


class AgentManager:
    """
    Central manager for all agent runtimes and groups.
    """

    def __init__(
        self,
        character_loader: CharacterLoader | None = None,
        llm_service: Any = None,
        memory_service: Any = None,
    ):
        self.character_loader = character_loader or CharacterLoader()
        self.llm_service = llm_service
        self.memory_service = memory_service

        self._runtimes: dict[str, AgentRuntime] = {}
        self._groups: dict[str, AgentGroup] = {}
        self._client_manager: ClientManager | None = None

    def create_agent(
        self,
        character_id: str,
        start: bool = False,
    ) -> AgentRuntime:
        """Create an agent runtime from a character"""
        character = self.character_loader.get_character(character_id)
        if not character:
            raise ValueError(f"Character not found: {character_id}")

        runtime = AgentRuntime(
            character=character,
            llm_service=self.llm_service,
            memory_service=self.memory_service,
        )

        self._runtimes[runtime.agent_id] = runtime
        logger.info(f"Created agent runtime for {character.name}")

        if start:
            asyncio.create_task(runtime.start())

        return runtime

    def get_agent(self, agent_id: str) -> AgentRuntime | None:
        """Get an agent runtime by ID"""
        return self._runtimes.get(agent_id)

    def list_agents(self) -> list[AgentRuntime]:
        """List all agent runtimes"""
        return list(self._runtimes.values())

    async def start_agent(self, agent_id: str) -> None:
        """Start an agent"""
        agent = self.get_agent(agent_id)
        if agent:
            await agent.start()

    async def stop_agent(self, agent_id: str) -> None:
        """Stop an agent"""
        agent = self.get_agent(agent_id)
        if agent:
            await agent.stop()

    async def stop_all(self) -> None:
        """Stop all agents"""
        for agent in self._runtimes.values():
            await agent.stop()

    def create_group(self, name: str) -> AgentGroup:
        """Create a new agent group"""
        group = AgentGroup(name)
        self._groups[group.group_id] = group
        return group

    def get_group(self, group_id: str) -> AgentGroup | None:
        """Get a group by ID"""
        return self._groups.get(group_id)

    def list_groups(self) -> list[AgentGroup]:
        """List all groups"""
        return list(self._groups.values())

    async def handle_platform_message(self, message: PlatformMessage) -> str | None:
        """Handle an incoming platform message with smart routing"""
        # Score agents by relevance
        scored_agents = []
        for agent in self._runtimes.values():
            if agent.is_running:
                score = self._calculate_agent_relevance(agent, message)
                if score > 0:
                    scored_agents.append((agent, score))

        # Sort by relevance (highest first)
        scored_agents.sort(key=lambda x: x[1], reverse=True)

        # Route to most relevant agent
        for agent, score in scored_agents:
            if score >= 0.3:  # Minimum relevance threshold
                return await agent.process_message(
                    message.content,
                    user_id=message.user_id,
                    platform=message.platform.value,
                    channel_id=message.channel_id,
                )

        return None

    def _calculate_agent_relevance(self, agent: AgentRuntime, message: PlatformMessage) -> float:
        """Calculate agent relevance score (0.0-1.0)"""
        score = 0.0
        char = agent.character
        content = message.content.lower()

        # Direct mention = highest priority
        if f"@{char.name.lower()}" in content:
            return 1.0

        # Topic match
        if char.topics:
            for topic in char.topics:
                if topic.lower() in content:
                    score += 0.35

        # Expertise/adjectives
        if hasattr(char, 'adjectives') and char.adjectives:
            for adj in char.adjectives:
                if adj.lower() in content:
                    score += 0.25

        return min(score, 0.9)

    async def _should_respond(self, agent: AgentRuntime, message: PlatformMessage) -> bool:
        """Determine if an agent should respond based on relevance"""
        return self._calculate_agent_relevance(agent, message) >= 0.3

    def get_status(self) -> dict[str, Any]:
        """Get overall status of all agents"""
        return {
            "agents": [
                {
                    "id": agent.agent_id,
                    "name": agent.character.name,
                    "status": agent.status.value,
                    "message_count": agent._context.message_count,
                    "last_activity": agent._context.last_activity.isoformat() if agent._context.last_activity else None,
                }
                for agent in self._runtimes.values()
            ],
            "groups": [
                {
                    "id": group.group_id,
                    "name": group.name,
                    "agent_count": len(group.get_agents()),
                }
                for group in self._groups.values()
            ],
        }


# Global agent manager
_agent_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    """Get the global agent manager"""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = AgentManager()
    return _agent_manager

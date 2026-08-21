"""
Multi-Platform Client System

Based on elizaOS platform integration architecture.
Provides unified interface for Discord, Twitter, Telegram, Farcaster, Slack clients.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PlatformType(str, Enum):
    """Supported platform types"""
    DISCORD = "discord"
    TELEGRAM = "telegram"
    FARCASTER = "farcaster"
    SLACK = "slack"
    DIRECT = "direct"


@dataclass
class PlatformMessage:
    """Unified message format across platforms"""
    id: str
    platform: PlatformType
    channel_id: str
    user_id: str
    user_name: str
    content: str
    timestamp: datetime
    reply_to: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_discord(cls, message: Any) -> "PlatformMessage":
        """Create from Discord message"""
        return cls(
            id=str(message.id),
            platform=PlatformType.DISCORD,
            channel_id=str(message.channel.id),
            user_id=str(message.author.id),
            user_name=message.author.name,
            content=message.content,
            timestamp=message.created_at,
            reply_to=str(message.reference.message_id) if message.reference else None,
            attachments=[{"url": a.url, "filename": a.filename} for a in message.attachments],
            metadata={"guild_id": str(message.guild.id) if message.guild else None},
        )

    @classmethod
    def from_telegram(cls, message: Any) -> "PlatformMessage":
        """Create from Telegram message"""
        return cls(
            id=str(message.message_id),
            platform=PlatformType.TELEGRAM,
            channel_id=str(message.chat.id),
            user_id=str(message.from_user.id),
            user_name=message.from_user.username or message.from_user.first_name,
            content=message.text or "",
            timestamp=datetime.fromtimestamp(message.date),
            metadata={"chat_type": message.chat.type},
        )

    @classmethod
    def from_twitter(cls, tweet: Any) -> "PlatformMessage":
        """Create from Twitter tweet"""
        return cls(
            id=str(tweet.id),
            platform=PlatformType.TWITTER,
            channel_id=str(tweet.conversation_id or tweet.id),
            user_id=str(tweet.author_id),
            user_name=tweet.author_username or "",
            content=tweet.text,
            timestamp=tweet.created_at,
            reply_to=str(tweet.in_reply_to_tweet_id) if tweet.in_reply_to_tweet_id else None,
            metadata={"tweet_type": tweet.tweet_type if hasattr(tweet, "tweet_type") else None},
        )


@dataclass
class PlatformResponse:
    """Response to send to platform"""
    content: str
    reply_to: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseClient(ABC):
    """
    Base class for platform clients.
    All platform clients must implement this interface.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._running = False
        self._message_handlers: list[callable] = []

    @property
    @abstractmethod
    def platform(self) -> PlatformType:
        """Return platform type"""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if client is connected"""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the platform"""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the platform"""

    @abstractmethod
    async def send_message(
        self,
        channel_id: str,
        content: str,
        reply_to: str | None = None,
        **kwargs: Any
    ) -> PlatformMessage:
        """Send a message to a channel"""

    @abstractmethod
    async def get_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[PlatformMessage]:
        """Get messages from a channel"""

    @abstractmethod
    async def listen(self) -> AsyncGenerator[PlatformMessage, None]:
        """Listen for incoming messages"""

    def on_message(self, handler: callable) -> None:
        """Register a message handler"""
        self._message_handlers.append(handler)

    async def _dispatch_message(self, message: PlatformMessage) -> None:
        """Dispatch message to all handlers"""
        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                logger.error("Message handler error", exc_info=True)

    async def start(self) -> None:
        """Start the client"""
        await self.connect()
        self._running = True

        async for message in self.listen():
            if not self._running:
                break
            await self._dispatch_message(message)

    async def stop(self) -> None:
        """Stop the client"""
        self._running = False
        await self.disconnect()


class DiscordClient(BaseClient):
    """Discord platform client"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._client = None

    @property
    def platform(self) -> PlatformType:
        return PlatformType.DISCORD

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_ready()

    async def connect(self) -> None:
        """Connect to Discord"""
        try:
            import discord

            intents = discord.Intents.default()
            intents.message_content = True
            intents.members = True

            self._client = discord.Client(intents=intents)

            @self._client.event
            async def on_message(message):
                if message.author.bot:
                    return
                platform_msg = PlatformMessage.from_discord(message)
                await self._dispatch_message(platform_msg)

            await self._client.start(self.config["bot_token"])
        except ImportError:
            logger.warning("discord.py not installed, Discord client unavailable")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    async def send_message(
        self,
        channel_id: str,
        content: str,
        reply_to: str | None = None,
        **kwargs: Any
    ) -> PlatformMessage:
        channel = self._client.get_channel(int(channel_id))
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        if reply_to:
            # Fetch the message to reply to
            reply_msg = await channel.fetch_message(int(reply_to))
            message = await reply_msg.reply(content)
        else:
            message = await channel.send(content)

        return PlatformMessage.from_discord(message)

    async def get_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[PlatformMessage]:
        channel = self._client.get_channel(int(channel_id))
        if not channel:
            return []

        before_msg = None
        if before:
            before_msg = await channel.fetch_message(int(before))

        messages = [msg async for msg in channel.history(limit=limit, before=before_msg)]
        return [PlatformMessage.from_discord(msg) for msg in messages]

    async def listen(self) -> AsyncGenerator[PlatformMessage, None]:
        # Discord uses event-based listening, not async generator
        # This is a placeholder for the interface
        while self._running:
            await asyncio.sleep(1)
            yield None  # type: ignore


class TelegramClient(BaseClient):
    """Telegram platform client"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._client = None
        self._application = None

    @property
    def platform(self) -> PlatformType:
        return PlatformType.TELEGRAM

    @property
    def is_connected(self) -> bool:
        return self._application is not None and self._application.running

    async def connect(self) -> None:
        """Connect to Telegram"""
        try:
            from telegram.ext import Application, MessageHandler, filters

            self._application = Application.builder().token(self.config["bot_token"]).build()

            async def handle_message(update, context):
                if update.message:
                    platform_msg = PlatformMessage.from_telegram(update.message)
                    await self._dispatch_message(platform_msg)

            self._application.add_handler(MessageHandler(filters.TEXT, handle_message))
            await self._application.initialize()
            await self._application.start()
            await self._application.updater.start_polling()
        except ImportError:
            logger.warning("python-telegram-bot not installed, Telegram client unavailable")

    async def disconnect(self) -> None:
        if self._application:
            await self._application.updater.stop()
            await self._application.stop()

    async def send_message(
        self,
        channel_id: str,
        content: str,
        reply_to: str | None = None,
        **kwargs: Any
    ) -> PlatformMessage:
        await self._application.bot.send_message(
            chat_id=int(channel_id),
            text=content,
            reply_to_message_id=int(reply_to) if reply_to else None,
        )
        # Return a mock message since we don't get the full message back
        return PlatformMessage(
            id="",
            platform=self.platform,
            channel_id=channel_id,
            user_id="bot",
            user_name="Bot",
            content=content,
            timestamp=datetime.utcnow(),
        )

    async def get_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[PlatformMessage]:
        # Telegram doesn't support easy message history retrieval
        return []

    async def listen(self) -> AsyncGenerator[PlatformMessage, None]:
        while self._running:
            await asyncio.sleep(1)
            yield None  # type: ignore


class ClientManager:
    """
    Manages multiple platform clients.
    Handles client lifecycle and message routing.
    """

    def __init__(self):
        self._clients: dict[PlatformType, BaseClient] = {}
        self._message_handlers: list[callable] = []

    def register_client(self, client: BaseClient) -> None:
        """Register a platform client"""
        self._clients[client.platform] = client
        client.on_message(self._handle_message)
        logger.info(f"Registered {client.platform} client")

    def get_client(self, platform: PlatformType) -> BaseClient | None:
        """Get a client by platform"""
        return self._clients.get(platform)

    def on_message(self, handler: callable) -> None:
        """Register a global message handler"""
        self._message_handlers.append(handler)

    async def _handle_message(self, message: PlatformMessage) -> None:
        """Handle incoming message from any platform"""
        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                logger.error("Global message handler error", exc_info=True)

    async def start_all(self) -> None:
        """Start all registered clients"""
        tasks = [client.start() for client in self._clients.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all registered clients"""
        for client in self._clients.values():
            await client.stop()

    async def broadcast(
        self,
        content: str,
        channels: dict[PlatformType, list[str]],
    ) -> list[PlatformMessage]:
        """Broadcast a message to multiple channels"""
        results = []
        for platform, channel_ids in channels.items():
            client = self._clients.get(platform)
            if not client:
                continue
            for channel_id in channel_ids:
                try:
                    msg = await client.send_message(channel_id, content)
                    results.append(msg)
                except Exception as e:
                    logger.error(f"Broadcast failed for {platform}:{channel_id}: {e}")
        return results


# Global client manager
_client_manager: ClientManager | None = None


def get_client_manager() -> ClientManager:
    """Get the global client manager"""
    global _client_manager
    if _client_manager is None:
        _client_manager = ClientManager()
    return _client_manager

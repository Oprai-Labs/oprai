"""
Character System Models

Based on elizaOS character file specification.
Defines AI character personality, knowledge, and behavior patterns.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ModelProvider(str, Enum):
    """Supported LLM providers"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LLAMA_LOCAL = "llama_local"
    OLLAMA = "ollama"
    GROK = "grok"
    GEMINI = "gemini"
    DEEPSEEK = "deepseek"
    LIVEPEER = "livepeer"


class ClientType(str, Enum):
    """Platform clients"""
    DISCORD = "discord"
    TWITTER = "twitter"
    TELEGRAM = "telegram"
    FARCASTER = "farcaster"
    SLACK = "slack"
    DIRECT = "direct"


class CharacterVoice(BaseModel):
    """Voice settings for text-to-speech"""
    model: str
    url: str | None = None


class CharacterStyle(BaseModel):
    """Style guidelines for different contexts"""
    all: list[str] = Field(default_factory=list)
    chat: list[str] = Field(default_factory=list)
    post: list[str] = Field(default_factory=list)


class MessageContent(BaseModel):
    """Message content structure"""
    text: str
    action: str | None = None
    attachments: list[dict[str, str]] | None = None


class MessageExample(BaseModel):
    """Message example for training interaction patterns"""
    user: str
    content: MessageContent


class CharacterSettings(BaseModel):
    """Settings and configuration"""
    secrets: dict[str, str] | None = None
    voice: CharacterVoice | None = None
    model: str | None = None
    embedding_model: str | None = Field(None, alias="embeddingModel")
    twitter_api_key: str | None = Field(None, alias="twitterApiKey")
    discord_bot_token: str | None = Field(None, alias="discordBotToken")
    telegram_bot_token: str | None = Field(None, alias="telegramBotToken")

    class Config:
        populate_by_name = True


class CharacterTemplates(BaseModel):
    """Prompt templates for various tasks"""
    goals_template: str | None = Field(None, alias="goalsTemplate")
    facts_template: str | None = Field(None, alias="factsTemplate")
    message_handler_template: str | None = Field(None, alias="messageHandlerTemplate")
    should_respond_template: str | None = Field(None, alias="shouldRespondTemplate")
    continue_message_handler_template: str | None = Field(None, alias="continueMessageHandlerTemplate")
    evaluation_template: str | None = Field(None, alias="evaluationTemplate")
    twitter_search_template: str | None = Field(None, alias="twitterSearchTemplate")
    twitter_post_template: str | None = Field(None, alias="twitterPostTemplate")
    twitter_message_handler_template: str | None = Field(None, alias="twitterMessageHandlerTemplate")
    twitter_should_respond_template: str | None = Field(None, alias="twitterShouldRespondTemplate")
    telegram_message_handler_template: str | None = Field(None, alias="telegramMessageHandlerTemplate")
    telegram_should_respond_template: str | None = Field(None, alias="telegramShouldRespondTemplate")
    discord_voice_handler_template: str | None = Field(None, alias="discordVoiceHandlerTemplate")
    discord_should_respond_template: str | None = Field(None, alias="discordShouldRespondTemplate")
    discord_message_handler_template: str | None = Field(None, alias="discordMessageHandlerTemplate")

    class Config:
        populate_by_name = True


class Character(BaseModel):
    """Complete Character definition"""
    id: str | None = None
    name: str
    model_provider: ModelProvider = Field(alias="modelProvider")
    clients: list[ClientType]
    bio: str | list[str]
    lore: list[str] | None = None
    knowledge: list[str] | None = None
    message_examples: list[list[MessageExample]] | None = Field(None, alias="messageExamples")
    post_examples: list[str] | None = Field(None, alias="postExamples")
    topics: list[str] | None = None
    adjectives: list[str] | None = None
    style: CharacterStyle | None = None
    settings: CharacterSettings | None = None
    templates: CharacterTemplates | None = None
    system_prompt: str | None = Field(None, alias="systemPrompt")
    active: bool | None = True
    created_at: datetime | None = Field(None, alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")
    owner_wallet: str | None = Field(None, alias="ownerWallet")
    is_public: bool | None = Field(None, alias="isPublic")
    tags: list[str] | None = None

    class Config:
        populate_by_name = True


class CharacterFile(Character):
    """Character file format for loading/saving"""
    version: str | None = None


class CreateCharacterRequest(BaseModel):
    """Character creation request"""
    name: str
    model_provider: ModelProvider = Field(alias="modelProvider")
    clients: list[ClientType]
    bio: str | list[str]
    lore: list[str] | None = None
    knowledge: list[str] | None = None
    message_examples: list[list[MessageExample]] | None = Field(None, alias="messageExamples")
    post_examples: list[str] | None = Field(None, alias="postExamples")
    topics: list[str] | None = None
    adjectives: list[str] | None = None
    style: CharacterStyle | None = None
    settings: CharacterSettings | None = None
    templates: CharacterTemplates | None = None
    system_prompt: str | None = Field(None, alias="systemPrompt")
    is_public: bool | None = Field(None, alias="isPublic")
    tags: list[str] | None = None

    class Config:
        populate_by_name = True


class UpdateCharacterRequest(BaseModel):
    """Character update request"""
    name: str | None = None
    model_provider: ModelProvider | None = Field(None, alias="modelProvider")
    clients: list[ClientType] | None = None
    bio: str | list[str] | None = None
    lore: list[str] | None = None
    knowledge: list[str] | None = None
    message_examples: list[list[MessageExample]] | None = Field(None, alias="messageExamples")
    post_examples: list[str] | None = Field(None, alias="postExamples")
    topics: list[str] | None = None
    adjectives: list[str] | None = None
    style: CharacterStyle | None = None
    settings: CharacterSettings | None = None
    templates: CharacterTemplates | None = None
    system_prompt: str | None = Field(None, alias="systemPrompt")
    active: bool | None = None
    is_public: bool | None = Field(None, alias="isPublic")
    tags: list[str] | None = None

    class Config:
        populate_by_name = True


class CharacterFilters(BaseModel):
    """Character list filters"""
    owner_wallet: str | None = Field(None, alias="ownerWallet")
    is_public: bool | None = Field(None, alias="isPublic")
    tags: list[str] | None = None
    search: str | None = None
    model_provider: ModelProvider | None = Field(None, alias="modelProvider")
    client: ClientType | None = None

    class Config:
        populate_by_name = True


class CharacterWithRuntime(Character):
    """Character with runtime info"""
    status: str = "idle"  # idle, running, error
    last_activity_at: datetime | None = Field(None, alias="lastActivityAt")
    message_count: int | None = Field(None, alias="messageCount")
    error_message: str | None = Field(None, alias="errorMessage")

    class Config:
        populate_by_name = True


# Built-in character templates
BUILTIN_CHARACTERS: list[dict[str, Any]] = [
    {
        "name": "Oprai Default",
        "modelProvider": "openai",
        "clients": ["direct"],
        "bio": [
            "A DeFi-native AI assistant specialized in Solana blockchain operations.",
            "Helps users navigate complex DeFi protocols with natural language commands.",
            "Expert in token swaps, staking, lending, and on-chain analytics.",
        ],
        "lore": [
            "Born from the need to simplify DeFi interactions on Solana.",
            "Trained on Solana protocol documentation and market data.",
        ],
        "topics": [
            "Solana", "DeFi", "cryptocurrency", "trading",
            "staking", "lending", "NFTs", "blockchain",
        ],
        "adjectives": ["knowledgeable", "helpful", "precise", "efficient", "friendly"],
        "style": {
            "all": [
                "Be concise and accurate",
                "Prioritize user safety in financial operations",
                "Explain complex concepts simply",
            ],
            "chat": [
                "Ask clarifying questions when needed",
                "Provide step-by-step guidance for transactions",
            ],
            "post": [
                "Share market insights",
                "Highlight DeFi opportunities",
            ],
        },
    },
    {
        "name": "DeFi Analyst",
        "modelProvider": "openai",
        "clients": ["direct", "twitter"],
        "bio": [
            "An analytical AI focused on DeFi market analysis and risk assessment.",
            "Provides data-driven insights on yield farming, liquidity pools, and token metrics.",
        ],
        "lore": [
            "Processes millions of on-chain transactions to identify patterns.",
            "Specialized in risk metrics and portfolio optimization.",
        ],
        "topics": [
            "yield farming", "liquidity mining", "TVL analysis",
            "APY tracking", "risk assessment", "portfolio management",
        ],
        "adjectives": ["analytical", "data-driven", "cautious", "insightful"],
        "style": {
            "all": [
                "Always cite data sources",
                "Present balanced views on risks and rewards",
                "Use charts and metrics when helpful",
            ],
            "chat": [
                "Provide detailed breakdowns",
                "Offer alternative strategies",
            ],
            "post": [
                "Share trending metrics",
                "Highlight unusual market activity",
            ],
        },
    },
    {
        "name": "NFT Curator",
        "modelProvider": "openai",
        "clients": ["direct", "twitter"],
        "bio": [
            "An AI specialized in NFT collections, art curation, and market trends.",
            "Helps users discover, analyze, and manage NFT portfolios.",
        ],
        "lore": [
            "Trained on art history and digital collectibles markets.",
            "Tracks floor prices, rarity scores, and collection metrics.",
        ],
        "topics": [
            "NFTs", "digital art", "collectibles",
            "floor prices", "rarity", "collection analysis",
        ],
        "adjectives": ["creative", "trendy", "knowledgeable", "visual"],
        "style": {
            "all": [
                "Appreciate artistic value alongside market metrics",
                "Stay current with trending collections",
            ],
            "chat": [
                "Provide detailed collection analysis",
                "Suggest similar collections based on preferences",
            ],
            "post": [
                "Spotlight emerging artists",
                "Share collection milestones",
            ],
        },
    },
]

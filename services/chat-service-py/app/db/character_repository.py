"""
Character Database Repository

PostgreSQL persistence for characters.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    String,
    Text,
    delete,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declarative_base

from app.models.character import (
    Character,
    CreateCharacterRequest,
    UpdateCharacterRequest,
)

logger = logging.getLogger(__name__)

Base = declarative_base()


class CharacterModel(Base):
    """SQLAlchemy model for characters"""
    __tablename__ = "characters"
    __table_args__ = (
        Index("ix_characters_owner_wallet", "owner_wallet"),
        Index("ix_characters_is_public", "is_public"),
        {"schema": "chat_schema"},
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    model_provider = Column(String(50), nullable=False)
    clients = Column(JSON, nullable=False, default=list)
    bio = Column(JSON, nullable=False)
    lore = Column(JSON, nullable=True)
    knowledge = Column(JSON, nullable=True)
    message_examples = Column(JSON, nullable=True)
    post_examples = Column(JSON, nullable=True)
    topics = Column(JSON, nullable=True)
    adjectives = Column(JSON, nullable=True)
    style = Column(JSON, nullable=True)
    settings = Column(JSON, nullable=True)
    templates = Column(JSON, nullable=True)
    system_prompt = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    owner_wallet = Column(String(255), nullable=True, index=True)
    is_public = Column(Boolean, default=False)
    tags = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CharacterRepository:
    """Repository for character persistence"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        request: CreateCharacterRequest,
        owner_wallet: str | None = None,
    ) -> Character:
        """Create a new character"""
        import uuid

        char_id = str(uuid.uuid4())
        now = datetime.utcnow()

        model = CharacterModel(
            id=char_id,
            name=request.name,
            model_provider=request.model_provider.value,
            clients=[c.value for c in request.clients],
            bio=request.bio if isinstance(request.bio, list) else [request.bio],
            lore=request.lore,
            knowledge=request.knowledge,
            message_examples=self._serialize_message_examples(request.message_examples),
            post_examples=request.post_examples,
            topics=request.topics,
            adjectives=request.adjectives,
            style=request.style.model_dump() if request.style else None,
            settings=request.settings.model_dump() if request.settings else None,
            templates=request.templates.model_dump() if request.templates else None,
            system_prompt=request.system_prompt,
            active=True,
            owner_wallet=owner_wallet,
            is_public=request.is_public or False,
            tags=request.tags,
            created_at=now,
            updated_at=now,
        )

        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        return self._model_to_character(model)

    async def get(self, character_id: str) -> Character | None:
        """Get a character by ID"""
        result = await self.session.execute(
            select(CharacterModel).where(CharacterModel.id == character_id)
        )
        model = result.scalar_one_or_none()

        if not model:
            return None

        return self._model_to_character(model)

    async def list(
        self,
        owner_wallet: str | None = None,
        is_public: bool | None = None,
        tags: list[str] | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Character], int]:
        """List characters with filters"""
        from sqlalchemy import func as sqlfunc

        # Build shared filter conditions once (reused for count and data queries)
        conditions = []
        if owner_wallet:
            conditions.append(CharacterModel.owner_wallet == owner_wallet)
        if is_public is not None:
            conditions.append(CharacterModel.is_public == is_public)
        if search:
            search_term = f"%{search}%"
            conditions.append(
                CharacterModel.name.ilike(search_term) |
                CharacterModel.bio.cast(Text).ilike(search_term)
            )

        # Count with scalar COUNT — never loads all IDs into memory
        count_query = select(sqlfunc.count(CharacterModel.id))
        for cond in conditions:
            count_query = count_query.where(cond)
        total_result = await self.session.execute(count_query)
        total = total_result.scalar_one()

        # Paginated data query
        data_query = select(CharacterModel)
        for cond in conditions:
            data_query = data_query.where(cond)
        data_query = data_query.order_by(CharacterModel.created_at.desc()).limit(limit).offset(offset)

        result = await self.session.execute(data_query)
        models = result.scalars().all()

        return [self._model_to_character(m) for m in models], total

    async def update(
        self,
        character_id: str,
        request: UpdateCharacterRequest,
        owner_wallet: str | None = None,
    ) -> Character | None:
        """Update a character"""
        # Get existing
        result = await self.session.execute(
            select(CharacterModel).where(CharacterModel.id == character_id)
        )
        model = result.scalar_one_or_none()

        if not model:
            return None

        # Check ownership
        if owner_wallet and model.owner_wallet != owner_wallet:
            return None

        # Update fields
        updates = request.model_dump(exclude_unset=True, by_alias=True)

        for field, value in updates.items():
            if field == "modelProvider":
                model.model_provider = value
            elif field == "clients":
                model.clients = [c.value if hasattr(c, 'value') else c for c in value]
            elif field == "messageExamples":
                model.message_examples = self._serialize_message_examples(value)
            elif field == "isPublic":
                model.is_public = value
            elif field == "systemPrompt":
                model.system_prompt = value
            elif hasattr(model, field):
                setattr(model, field, value)

        model.updated_at = datetime.utcnow()

        await self.session.commit()
        await self.session.refresh(model)

        return self._model_to_character(model)

    async def delete(
        self,
        character_id: str,
        owner_wallet: str | None = None,
    ) -> bool:
        """Delete a character"""
        # Check ownership first
        if owner_wallet:
            result = await self.session.execute(
                select(CharacterModel.owner_wallet).where(
                    CharacterModel.id == character_id
                )
            )
            owner = result.scalar_one_or_none()
            if owner != owner_wallet:
                return False

        result = await self.session.execute(
            delete(CharacterModel).where(CharacterModel.id == character_id)
        )

        await self.session.commit()
        return result.rowcount > 0

    async def duplicate(
        self,
        character_id: str,
        owner_wallet: str | None = None,
    ) -> Character | None:
        """Duplicate a character"""
        original = await self.get(character_id)
        if not original:
            return None

        import uuid
        now = datetime.utcnow()

        new_model = CharacterModel(
            id=str(uuid.uuid4()),
            name=f"{original.name} (Copy)",
            model_provider=original.model_provider.value,
            clients=[c.value for c in original.clients],
            bio=original.bio if isinstance(original.bio, list) else [original.bio],
            lore=original.lore,
            knowledge=original.knowledge,
            message_examples=self._serialize_message_examples(original.message_examples),
            post_examples=original.post_examples,
            topics=original.topics,
            adjectives=original.adjectives,
            style=original.style.model_dump() if original.style else None,
            settings=original.settings.model_dump() if original.settings else None,
            templates=original.templates.model_dump() if original.templates else None,
            system_prompt=original.system_prompt,
            active=True,
            owner_wallet=owner_wallet,
            is_public=False,
            tags=original.tags,
            created_at=now,
            updated_at=now,
        )

        self.session.add(new_model)
        await self.session.commit()
        await self.session.refresh(new_model)

        return self._model_to_character(new_model)

    def _model_to_character(self, model: CharacterModel) -> Character:
        """Convert SQLAlchemy model to Pydantic model"""
        from app.models.character import (
            CharacterSettings,
            CharacterStyle,
            CharacterTemplates,
            ClientType,
            MessageContent,
            MessageExample,
            ModelProvider,
        )

        # Parse clients
        clients = []
        for c in model.clients:
            if isinstance(c, str):
                try:
                    clients.append(ClientType(c))
                except ValueError:
                    pass

        # Parse style
        style = None
        if model.style:
            style = CharacterStyle(**model.style)

        # Parse settings
        settings = None
        if model.settings:
            settings = CharacterSettings(**model.settings)

        # Parse templates
        templates = None
        if model.templates:
            templates = CharacterTemplates(**model.templates)

        # Parse message examples
        message_examples = None
        if model.message_examples:
            message_examples = []
            for group in model.message_examples:
                parsed_group = []
                for ex in group:
                    if isinstance(ex, dict):
                        parsed_group.append(MessageExample(
                            user=ex.get("user", ""),
                            content=MessageContent(**ex.get("content", {"text": ""})),
                        ))
                if parsed_group:
                    message_examples.append(parsed_group)

        return Character(
            id=model.id,
            name=model.name,
            model_provider=ModelProvider(model.model_provider),
            clients=clients,
            bio=model.bio,
            lore=model.lore,
            knowledge=model.knowledge,
            message_examples=message_examples,
            post_examples=model.post_examples,
            topics=model.topics,
            adjectives=model.adjectives,
            style=style,
            settings=settings,
            templates=templates,
            system_prompt=model.system_prompt,
            active=model.active,
            owner_wallet=model.owner_wallet,
            is_public=model.is_public,
            tags=model.tags,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _serialize_message_examples(self, examples: Any) -> Any:
        """Serialize message examples for storage"""
        if not examples:
            return None

        result = []
        for group in examples:
            serialized_group = []
            for ex in group:
                if hasattr(ex, 'model_dump'):
                    serialized_group.append(ex.model_dump())
                elif isinstance(ex, dict):
                    serialized_group.append(ex)
            if serialized_group:
                result.append(serialized_group)

        return result if result else None


# Migration for characters table
CHARACTER_MIGRATION = """
-- Create chat_schema if not exists
CREATE SCHEMA IF NOT EXISTS chat_schema;

-- Create characters table
CREATE TABLE IF NOT EXISTS chat_schema.characters (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    model_provider VARCHAR(50) NOT NULL,
    clients JSONB NOT NULL DEFAULT '[]',
    bio JSONB NOT NULL,
    lore JSONB,
    knowledge JSONB,
    message_examples JSONB,
    post_examples JSONB,
    topics JSONB,
    adjectives JSONB,
    style JSONB,
    settings JSONB,
    templates JSONB,
    system_prompt TEXT,
    active BOOLEAN DEFAULT TRUE,
    owner_wallet VARCHAR(255),
    is_public BOOLEAN DEFAULT FALSE,
    tags JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes
CREATE INDEX IF NOT EXISTS ix_characters_owner_wallet ON chat_schema.characters (owner_wallet);
CREATE INDEX IF NOT EXISTS ix_characters_is_public ON chat_schema.characters (is_public) WHERE is_public = TRUE;
CREATE INDEX IF NOT EXISTS ix_characters_name ON chat_schema.characters (name);
CREATE INDEX IF NOT EXISTS ix_characters_active ON chat_schema.characters (active) WHERE active = TRUE;
-- GIN index for JSONB bio search (supports ILIKE via cast and @> operators)
CREATE INDEX IF NOT EXISTS ix_characters_bio_gin ON chat_schema.characters USING GIN (bio);
CREATE INDEX IF NOT EXISTS ix_characters_tags_gin ON chat_schema.characters USING GIN (tags);

-- Create update trigger
CREATE OR REPLACE FUNCTION update_character_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_character_timestamp ON chat_schema.characters;
CREATE TRIGGER trigger_update_character_timestamp
    BEFORE UPDATE ON chat_schema.characters
    FOR EACH ROW
    EXECUTE FUNCTION update_character_timestamp();
"""

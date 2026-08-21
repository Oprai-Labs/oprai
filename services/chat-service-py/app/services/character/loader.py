"""
Character Loader Service

Handles loading, caching, and managing character configurations.
"""

from __future__ import annotations

import json
import logging
import random
import re
import socket
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.models.character import (
    Character,
    CharacterFile,
    CharacterStyle,
    CreateCharacterRequest,
    ModelProvider,
    ClientType,
    BUILTIN_CHARACTERS,
)

logger = logging.getLogger(__name__)

_MAX_SYSTEM_PROMPT_LEN = 8_000  # chars — prevents token flooding via character prompts


def _sanitize_system_prompt(text: str | None, field_name: str = "system_prompt") -> str | None:
    """Validate and sanitise a user-supplied system prompt for a character.

    Normalises and length-caps. There is deliberately no phrase blocklist: this
    text is a persona, and prompt_builder now nests it inside our own scaffold
    under an explicit boundary (style only, no capabilities, no lifting of
    restrictions), so it is bounded by where it sits rather than by whether the
    author avoided five English phrases someone thought to write down.
    """
    if text is None:
        return None
    text = unicodedata.normalize("NFKC", text)
    if len(text) > _MAX_SYSTEM_PROMPT_LEN:
        logger.warning(
            "Character %s truncated from %d to %d chars",
            field_name, len(text), _MAX_SYSTEM_PROMPT_LEN,
        )
        text = text[:_MAX_SYSTEM_PROMPT_LEN]
    return text


class CharacterLoader:
    """Service for loading and managing character configurations"""

    def __init__(self, characters_dir: Optional[Path] = None):
        """
        Initialize character loader.

        Args:
            characters_dir: Directory containing character JSON files
        """
        self.characters_dir = characters_dir or Path("characters")
        self._cache: dict[str, Character] = {}
        self._builtin_ids: set[str] = set()

        # Load built-in characters
        self._load_builtins()

    def _load_builtins(self) -> None:
        """Load built-in character templates"""
        for i, char_data in enumerate(BUILTIN_CHARACTERS):
            char_id = f"builtin-{i}"
            char_data["id"] = char_id
            char_data["active"] = True
            char_data["createdAt"] = datetime.utcnow().isoformat()
            char_data["isPublic"] = True

            try:
                character = Character(**char_data)
                self._cache[char_id] = character
                self._builtin_ids.add(char_id)
                logger.info(f"Loaded built-in character: {character.name}")
            except Exception as e:
                logger.error("Failed to load built-in character {i}", exc_info=True)

    def load_from_file(self, path: Path) -> Character:
        """
        Load a character from a JSON file.

        Args:
            path: Path to character JSON file

        Returns:
            Character instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is invalid
        """
        if not path.exists():
            raise FileNotFoundError(f"Character file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate required fields
        if not data.get("name"):
            raise ValueError("Character file missing 'name' field")
        if not data.get("modelProvider"):
            raise ValueError("Character file missing 'modelProvider' field")
        if not data.get("clients"):
            raise ValueError("Character file missing 'clients' field")

        # Generate ID if not present
        if not data.get("id"):
            data["id"] = str(uuid.uuid4())

        character = Character(**data)
        self._cache[character.id] = character

        logger.info(f"Loaded character from file: {character.name} ({character.id})")
        return character

    def get_character(self, character_id: str) -> Optional[Character]:
        """
        Get a character by ID.

        Args:
            character_id: Character ID

        Returns:
            Character instance or None if not found
        """
        return self._cache.get(character_id)

    def list_characters(
        self,
        owner_wallet: Optional[str] = None,
        is_public: Optional[bool] = None,
        tags: Optional[list[str]] = None,
        search: Optional[str] = None,
    ) -> list[Character]:
        """
        List characters with optional filters.

        Args:
            owner_wallet: Filter by owner wallet
            is_public: Filter by public status
            tags: Filter by tags
            search: Search in name, bio, topics

        Returns:
            List of matching characters
        """
        results = []

        for char in self._cache.values():
            # Apply filters
            if owner_wallet and char.owner_wallet != owner_wallet:
                continue
            if is_public is not None and char.is_public != is_public:
                continue
            if tags:
                if not char.tags or not any(t in char.tags for t in tags):
                    continue
            if search:
                search_lower = search.lower()
                searchable = " ".join([
                    char.name,
                    *(char.bio if isinstance(char.bio, list) else [char.bio]),
                    *(char.topics or []),
                    *(char.tags or []),
                ]).lower()
                if search_lower not in searchable:
                    continue

            results.append(char)

        return results

    def create_character(
        self,
        request: CreateCharacterRequest,
        owner_wallet: Optional[str] = None,
    ) -> Character:
        """
        Create a new character.

        Args:
            request: Character creation request
            owner_wallet: Owner wallet address

        Returns:
            Created character
        """
        char_id = str(uuid.uuid4())
        now = datetime.utcnow()

        safe_system_prompt = _sanitize_system_prompt(request.system_prompt)

        character = Character(
            id=char_id,
            name=request.name,
            model_provider=request.model_provider,
            clients=request.clients,
            bio=request.bio,
            lore=request.lore,
            knowledge=request.knowledge,
            message_examples=request.message_examples,
            post_examples=request.post_examples,
            topics=request.topics,
            adjectives=request.adjectives,
            style=request.style,
            settings=request.settings,
            templates=request.templates,
            system_prompt=safe_system_prompt,
            active=True,
            created_at=now,
            updated_at=now,
            owner_wallet=owner_wallet,
            is_public=request.is_public or False,
            tags=request.tags,
        )

        self._cache[char_id] = character
        logger.info(f"Created character: {character.name} ({char_id})")

        return character

    def update_character(
        self,
        character_id: str,
        updates: dict[str, Any],
    ) -> Optional[Character]:
        """
        Update a character.

        Args:
            character_id: Character ID
            updates: Fields to update

        Returns:
            Updated character or None if not found
        """
        character = self._cache.get(character_id)
        if not character:
            return None

        # Don't allow updating built-in characters
        if character_id in self._builtin_ids:
            raise ValueError("Cannot modify built-in characters")

        # Apply updates
        update_data = character.model_dump()
        update_data.update(updates)
        update_data["updated_at"] = datetime.utcnow()

        # Handle alias fields
        if "modelProvider" in updates:
            update_data["model_provider"] = updates["modelProvider"]
        if "isPublic" in updates:
            update_data["is_public"] = updates["isPublic"]
        if "ownerWallet" in updates:
            update_data["owner_wallet"] = updates["ownerWallet"]
        if "systemPrompt" in updates:
            update_data["system_prompt"] = _sanitize_system_prompt(
                updates["systemPrompt"], "systemPrompt"
            )
        if "system_prompt" in updates:
            update_data["system_prompt"] = _sanitize_system_prompt(
                updates["system_prompt"], "system_prompt"
            )

        updated_character = Character(**update_data)
        self._cache[character_id] = updated_character

        logger.info(f"Updated character: {updated_character.name} ({character_id})")
        return updated_character

    def delete_character(self, character_id: str) -> bool:
        """
        Delete a character.

        Args:
            character_id: Character ID

        Returns:
            True if deleted, False if not found
        """
        if character_id in self._builtin_ids:
            raise ValueError("Cannot delete built-in characters")

        if character_id not in self._cache:
            return False

        del self._cache[character_id]
        logger.info(f"Deleted character: {character_id}")
        return True

    def duplicate_character(self, character_id: str) -> Optional[Character]:
        """
        Duplicate a character.

        Args:
            character_id: Character ID to duplicate

        Returns:
            New character or None if not found
        """
        original = self._cache.get(character_id)
        if not original:
            return None

        new_id = str(uuid.uuid4())
        now = datetime.utcnow()

        new_data = original.model_dump()
        new_data["id"] = new_id
        new_data["name"] = f"{original.name} (Copy)"
        new_data["created_at"] = now
        new_data["updated_at"] = now
        new_data["is_public"] = False

        new_character = Character(**new_data)
        self._cache[new_id] = new_character

        logger.info(f"Duplicated character: {original.name} -> {new_character.name}")
        return new_character

    def get_random_bio(self, character: Character) -> str:
        """
        Get a random bio statement for variety.

        Args:
            character: Character instance

        Returns:
            Random bio string
        """
        if isinstance(character.bio, list):
            return random.choice(character.bio)
        return character.bio

    def get_random_lore(self, character: Character) -> Optional[str]:
        """
        Get a random lore element for variety.

        Args:
            character: Character instance

        Returns:
            Random lore string or None
        """
        if character.lore:
            return random.choice(character.lore)
        return None

    def get_style_instructions(self, character: Character, context: str = "all") -> str:
        """
        Get style instructions for a given context.

        Args:
            character: Character instance
            context: Context type (all, chat, post)

        Returns:
            Style instructions string
        """
        if not character.style:
            return ""

        instructions = list(character.style.all)

        if context == "chat" and character.style.chat:
            instructions.extend(character.style.chat)
        elif context == "post" and character.style.post:
            instructions.extend(character.style.post)

        return "\n".join(f"- {i}" for i in instructions)


# Global character loader instance
_character_loader: Optional[CharacterLoader] = None


def get_character_loader() -> CharacterLoader:
    """Get the global character loader instance"""
    global _character_loader
    if _character_loader is None:
        _character_loader = CharacterLoader()
    return _character_loader

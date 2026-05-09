"""
Character Management API Endpoints

REST API for managing AI character configurations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse

from app.middleware.auth import require_auth
from app.models.character import (
    Character,
    CharacterFile,
    CharacterWithRuntime,
    CreateCharacterRequest,
    UpdateCharacterRequest,
    CharacterFilters,
)
from app.services.character import get_character_loader, CharacterLoader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/characters", tags=["characters"])

# ── Runtime state ──────────────────────────────────────────────────────────────
# In-memory map of character_id → runtime state dict.
# For multi-process deployments this should migrate to Redis or a DB table.
_RUNTIME_STATE: dict[str, dict] = {}
# Key: "character_id", Value: {"status": "running"|"idle", "started_at": str, "message_count": int}


def _runtime_key(wallet: str, character_id: str) -> str:
    return f"{wallet}:{character_id}"


def _get_runtime(wallet: str, character_id: str) -> dict:
    key = _runtime_key(wallet, character_id)
    return _RUNTIME_STATE.get(key, {"status": "idle", "started_at": None, "message_count": 0})


def get_loader() -> CharacterLoader:
    """Get character loader dependency"""
    return get_character_loader()


@router.get("")
async def list_characters(
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
    owner_wallet: Optional[str] = Query(None, alias="ownerWallet"),
    is_public: Optional[bool] = Query(None, alias="isPublic"),
    tags: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    """
    List characters with optional filters.

    - **ownerWallet**: Filter by owner wallet address
    - **isPublic**: Filter by public status
    - **tags**: Comma-separated list of tags
    - **search**: Search in name, bio, topics
    - **limit**: Maximum number of results
    - **offset**: Offset for pagination
    """
    tag_list = tags.split(",") if tags else None

    characters = loader.list_characters(
        owner_wallet=owner_wallet,
        is_public=is_public,
        tags=tag_list,
        search=search,
    )

    total = len(characters)
    paginated = characters[offset:offset + limit]

    return {
        "characters": [char.model_dump(by_alias=True) for char in paginated],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/templates")
async def get_public_templates(
    loader: CharacterLoader = Depends(get_loader),
) -> list[dict]:
    """Get public character templates"""
    characters = loader.list_characters(is_public=True)
    return [char.model_dump(by_alias=True) for char in characters]


@router.get("/{character_id}")
async def get_character(
    character_id: str,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Get a single character by ID"""
    character = loader.get_character(character_id)

    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    return character.model_dump(by_alias=True)


@router.post("")
async def create_character(
    request: CreateCharacterRequest,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """
    Create a new character.

    - **name**: Character display name
    - **modelProvider**: AI model provider (openai, anthropic, etc.)
    - **clients**: Platform clients (discord, twitter, telegram, etc.)
    - **bio**: Character biography (string or array for randomization)
    - **lore**: Backstory elements
    - **knowledge**: Knowledge base for RAG
    - **messageExamples**: Sample conversations
    - **postExamples**: Sample social media posts
    - **topics**: Topics the character is interested in
    - **adjectives**: Words describing character traits
    - **style**: Style guidelines for different contexts
    - **settings**: Configuration and secrets
    - **templates**: Custom prompt templates
    - **systemPrompt**: System prompt override
    - **isPublic**: Whether character is publicly visible
    - **tags**: Tags for categorization
    """
    try:
        character = loader.create_character(request, owner_wallet=wallet)
        return character.model_dump(by_alias=True)
    except Exception as e:
        logger.error("Failed to create character", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid request")


@router.patch("/{character_id}")
async def update_character(
    character_id: str,
    request: UpdateCharacterRequest,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Update a character"""
    # Check ownership
    character = loader.get_character(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    if character.owner_wallet != wallet:
        raise HTTPException(status_code=403, detail="Not authorized to modify this character")

    try:
        updates = request.model_dump(exclude_unset=True, by_alias=True)
        updated = loader.update_character(character_id, updates)

        if not updated:
            raise HTTPException(status_code=404, detail="Character not found")

        return updated.model_dump(by_alias=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.delete("/{character_id}")
async def delete_character(
    character_id: str,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Delete a character"""
    # Check ownership
    character = loader.get_character(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    if character.owner_wallet != wallet:
        raise HTTPException(status_code=403, detail="Not authorized to delete this character")

    try:
        deleted = loader.delete_character(character_id)
        return {"ok": deleted}
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.post("/{character_id}/duplicate")
async def duplicate_character(
    character_id: str,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Duplicate a character"""
    try:
        duplicated = loader.duplicate_character(character_id)

        if not duplicated:
            raise HTTPException(status_code=404, detail="Character not found")

        # Set new owner
        updates = {"owner_wallet": wallet}
        loader.update_character(duplicated.id, updates)

        return duplicated.model_dump(by_alias=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.post("/{character_id}/export")
async def export_character(
    character_id: str,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> JSONResponse:
    """Export character to JSON file"""
    character = loader.get_character(character_id)

    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    export_data = CharacterFile(**character.model_dump(), version="1.0.0")

    return JSONResponse(
        content=export_data.model_dump(by_alias=True, mode="json"),
        headers={
            "Content-Disposition": f'attachment; filename="{character.name.lower().replace(" ", "_")}.json"'
        }
    )


@router.post("/import")
async def import_character(
    file: UploadFile = File(...),
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Import character from JSON file"""
    try:
        content = await file.read()
        data = json.loads(content)

        # Validate required fields
        if not data.get("name"):
            raise ValueError("Character file missing 'name' field")
        if not data.get("modelProvider"):
            raise ValueError("Character file missing 'modelProvider' field")
        if not data.get("clients"):
            raise ValueError("Character file missing 'clients' field")

        # Create from imported data
        request = CreateCharacterRequest(
            name=data["name"],
            model_provider=data["modelProvider"],
            clients=data["clients"],
            bio=data.get("bio", ""),
            lore=data.get("lore"),
            knowledge=data.get("knowledge"),
            message_examples=data.get("messageExamples"),
            post_examples=data.get("postExamples"),
            topics=data.get("topics"),
            adjectives=data.get("adjectives"),
            style=data.get("style"),
            settings=data.get("settings"),
            templates=data.get("templates"),
            system_prompt=data.get("systemPrompt"),
            is_public=False,
            tags=data.get("tags"),
        )

        character = loader.create_character(request, owner_wallet=wallet)
        return character.model_dump(by_alias=True)

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")
    except Exception as e:
        logger.error("Failed to import character", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to import character")


@router.get("/{character_id}/runtime")
async def get_character_runtime(
    character_id: str,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Get character runtime status"""
    character = loader.get_character(character_id)

    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    state = _get_runtime(wallet, character_id)
    runtime = CharacterWithRuntime(
        **character.model_dump(),
        status=state["status"],
        last_activity_at=state.get("started_at") or character.updated_at,
        message_count=state.get("message_count", 0),
    )
    return runtime.model_dump(by_alias=True)


@router.post("/{character_id}/start")
async def start_character(
    character_id: str,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Start character runtime"""
    character = loader.get_character(character_id)

    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    key = _runtime_key(wallet, character_id)
    state = _RUNTIME_STATE.get(key, {})

    if state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Character runtime is already running")

    now = datetime.now(timezone.utc).isoformat()
    _RUNTIME_STATE[key] = {
        "status": "running",
        "started_at": now,
        "message_count": state.get("message_count", 0),
    }
    logger.info("Character runtime started: %s (wallet=%s)", character_id, wallet[:8])

    runtime = CharacterWithRuntime(
        **character.model_dump(),
        status="running",
        last_activity_at=now,
        message_count=_RUNTIME_STATE[key]["message_count"],
    )
    return runtime.model_dump(by_alias=True)


@router.post("/{character_id}/stop")
async def stop_character(
    character_id: str,
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """Stop character runtime"""
    character = loader.get_character(character_id)

    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    key = _runtime_key(wallet, character_id)
    state = _RUNTIME_STATE.get(key, {})

    if state.get("status") != "running":
        raise HTTPException(status_code=409, detail="Character runtime is not running")

    now = datetime.now(timezone.utc).isoformat()
    _RUNTIME_STATE[key] = {
        "status": "idle",
        "started_at": None,
        "message_count": state.get("message_count", 0),
    }
    logger.info("Character runtime stopped: %s (wallet=%s)", character_id, wallet[:8])

    runtime = CharacterWithRuntime(
        **character.model_dump(),
        status="idle",
        last_activity_at=now,
        message_count=_RUNTIME_STATE[key]["message_count"],
    )
    return runtime.model_dump(by_alias=True)


@router.post("/{character_id}/prompt")
async def generate_prompt(
    character_id: str,
    context: str = Query("system", description="Prompt context (system, chat, post)"),
    topic: Optional[str] = Query(None, description="Topic for post generation"),
    wallet: str = Depends(require_auth),
    loader: CharacterLoader = Depends(get_loader),
) -> dict:
    """
    Generate a prompt for the character.

    - **context**: Prompt context (system, chat, post)
    - **topic**: Topic for post generation (only used when context=post)
    """
    from app.services.character import PromptBuilder

    character = loader.get_character(character_id)

    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    builder = PromptBuilder(character)

    if context == "system":
        prompt = builder.build_system_prompt()
    elif context == "chat":
        prompt = builder.build_message_handler_prompt()
    elif context == "post":
        prompt = builder.build_twitter_post_prompt(topic=topic or "")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown context: {context}")

    return {"prompt": prompt}

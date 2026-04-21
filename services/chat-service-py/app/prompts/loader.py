"""
Prompt loader module for OPRAI Chat Service.

Loads and caches modular prompt files at startup.
Optimized for performance - reads files once and caches in memory.

Usage:
    from app.prompts.loader import get_prompt_loader, get_system_prompt

    # Get the cached prompt (fast - no disk I/O)
    prompt = get_system_prompt()

Performance:
    - Disk I/O happens only once at application startup
    - Subsequent calls use in-memory cache
    - Singleton pattern ensures single instance across the application
"""

import logging
from pathlib import Path
from typing import Optional

__all__ = ["PromptLoader", "get_prompt_loader", "get_system_prompt"]

logger = logging.getLogger(__name__)


class PromptLoader:
    """
    Singleton prompt loader with startup caching.

    Loads all modular prompt files once at initialization and caches
    the combined system prompt in memory for fast access.
    """

    _instance: Optional['PromptLoader'] = None
    _cached_prompt: Optional[str] = None
    _initialized: bool = False

    # Prompt files in loading order (order matters for context)
    PROMPT_FILES = [
        "solana_action_base.txt",          # Core personality, expertise, formatting
        "solana_action_queries.txt",       # QUERY definitions
        "solana_action_core.txt",          # Transfer, swap, stake, burn, etc.
        "solana_action_dex.txt",           # Jupiter, Raydium, Orca, Meteora
        "solana_action_lending.txt",       # Kamino, MarginFi, Solend
        "solana_action_staking.txt",       # Marinade, Jito
        "solana_action_nft.txt",           # Tensor, Magic Eden, PumpFun
        "solana_action_crosschain.txt",    # Bridge, Wormhole, Relay
        "solana_action_streamflow.txt",    # Streamflow token streaming & vesting
    ]

    def __new__(cls) -> 'PromptLoader':
        """Singleton pattern - only one instance exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize only once."""
        if not PromptLoader._initialized:
            self._load_prompts()
            PromptLoader._initialized = True

    def _load_prompts(self) -> None:
        """Load and cache all prompt files at startup."""
        # Files are in the same directory as this loader module
        prompts_dir = Path(__file__).parent
        loaded_parts: list[tuple[str, str]] = []
        missing_files: list[str] = []

        for filename in self.PROMPT_FILES:
            file_path = prompts_dir / filename
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    loaded_parts.append((filename, content))
                    logger.debug(f"Loaded prompt file: {filename} ({len(content)} chars)")
                except Exception as e:
                    logger.error("Failed to load {filename}", exc_info=True)
                    missing_files.append(filename)
            else:
                logger.warning(f"Prompt file not found: {filename}")
                missing_files.append(filename)

        if missing_files:
            logger.error(f"Missing prompt files: {missing_files}")
            # Fallback to legacy file if it exists
            legacy_path = prompts_dir / "solana_action.txt"
            if legacy_path.exists():
                logger.info("Falling back to legacy solana_action.txt")
                self._cached_prompt = legacy_path.read_text(encoding="utf-8")
            else:
                logger.critical("No prompt files available!")
                self._cached_prompt = ""
            return

        # Combine all parts with double newlines
        self._cached_prompt = "\n\n".join(content for _, content in loaded_parts)
        logger.info(
            f"Prompts loaded successfully: {len(loaded_parts)} files, "
            f"{len(self._cached_prompt)} total chars"
        )

    def get_system_prompt(self) -> str:
        """
        Get the cached system prompt.

        Returns:
            Combined system prompt string from all modular files.
        """
        if self._cached_prompt is None:
            # Should not happen, but handle gracefully
            logger.warning("Prompt cache empty, reloading...")
            self._load_prompts()
        return self._cached_prompt or ""

    @property
    def prompt_length(self) -> int:
        """Get total character count of cached prompt."""
        return len(self._cached_prompt) if self._cached_prompt else 0

    @property
    def is_loaded(self) -> bool:
        """Check if prompts are loaded successfully."""
        return self._cached_prompt is not None and len(self._cached_prompt) > 0


def get_prompt_loader() -> PromptLoader:
    """
    Get the singleton prompt loader instance.

    Returns:
        The cached PromptLoader instance.
    """
    return PromptLoader()


def get_system_prompt() -> str:
    """
    Convenience function to get the system prompt.

    Returns:
        Combined system prompt string.
    """
    return get_prompt_loader().get_system_prompt()

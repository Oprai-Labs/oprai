"""
Prompt loader module for OPRAI Chat Service.

Loads and caches modular prompt files at startup.
Supports protocol-scoped prompts to minimize token usage.
"""

import logging
import os
from pathlib import Path
from typing import Optional

__all__ = ["PromptLoader", "get_prompt_loader", "get_system_prompt"]

logger = logging.getLogger(__name__)

# Protocol → prompt files that must be included (in addition to _always files).
# Files are merged in PROMPT_FILES order so context stays coherent.
PROTOCOL_FILE_MAP: dict[str, list[str]] = {
    # DEX / Swap
    "jupiter":      ["solana_action_core.txt", "solana_action_dex.txt", "solana_action_market_data.txt"],
    "raydium":      ["solana_action_core.txt", "solana_action_dex.txt"],
    "orca":         ["solana_action_core.txt", "solana_action_dex.txt"],
    "meteora":      ["solana_action_core.txt", "solana_action_dex.txt"],
    # Liquid Staking
    "marinade":     ["solana_action_staking.txt"],
    "jito":         ["solana_action_staking.txt"],
    "jupsol":       ["solana_action_staking.txt"],
    "native_stake": ["solana_action_staking.txt"],
    # Lending / Borrowing
    "kamino":       ["solana_action_queries.txt", "solana_action_lending.txt"],
    "marginfi":     ["solana_action_queries.txt", "solana_action_lending.txt"],
    "solend":       ["solana_action_queries.txt", "solana_action_lending.txt"],
    # NFT / Token launches
    "tensor":       ["solana_action_nft.txt"],
    "magic_eden":   ["solana_action_nft.txt"],
    "pumpfun":      ["solana_action_nft.txt"],
    # Cross-chain bridges
    "relay":        ["solana_action_crosschain.txt"],
    "debridge":     ["solana_action_crosschain.txt"],
    "squid":        ["solana_action_crosschain.txt"],
    # Token streaming / vesting
    "streamflow":   ["solana_action_streamflow.txt"],
}

# Always loaded — personality, formatting, base action grammar
_ALWAYS_FILES = ["solana_action_base.txt"]

# Fallback when an unknown protocol is selected
_FALLBACK_FILES = ["solana_action_core.txt", "solana_action_queries.txt"]


class PromptLoader:
    """
    Singleton prompt loader with mtime-based hot-reload.

    Caches individual prompt files and the combined full prompt.
    Automatically reloads if any prompt file changes on disk — no service
    restart required after editing .txt files.
    """

    _instance: Optional["PromptLoader"] = None
    _initialized: bool = False

    # Ordered file list (order matters for LLM context)
    PROMPT_FILES = [
        "solana_action_base.txt",
        "solana_action_queries.txt",
        "solana_action_core.txt",
        "solana_action_dex.txt",
        "solana_action_lending.txt",
        "solana_action_staking.txt",
        "solana_action_nft.txt",
        "solana_action_crosschain.txt",
        "solana_action_streamflow.txt",
        "solana_action_market_data.txt",
        "solana_action_knowledge.txt",
        "solana_action_strategy.txt",
    ]

    def __new__(cls) -> "PromptLoader":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not PromptLoader._initialized:
            self._file_cache: dict[str, str] = {}
            self._full_prompt: str = ""
            self._mtimes: dict[str, float] = {}
            self._load_prompts()
            PromptLoader._initialized = True

    def _load_prompts(self) -> None:
        prompts_dir = Path(__file__).parent
        missing: list[str] = []

        for filename in self.PROMPT_FILES:
            path = prompts_dir / filename
            if path.exists():
                try:
                    self._file_cache[filename] = path.read_text(encoding="utf-8")
                    self._mtimes[filename] = os.path.getmtime(path)
                except Exception:
                    logger.error("Failed to load %s", filename, exc_info=True)
                    missing.append(filename)
            else:
                logger.warning("Prompt file not found: %s", filename)
                missing.append(filename)

        if missing:
            raise FileNotFoundError(f"Missing prompt files: {missing}")

        self._full_prompt = "\n\n".join(
            self._file_cache[f] for f in self.PROMPT_FILES if f in self._file_cache
        )
        total_kb = len(self._full_prompt) / 1024
        logger.info(
            "Prompts loaded: %d files, %.1f KB total",
            len(self._file_cache),
            total_kb,
        )

    def _check_reload(self) -> None:
        """Reload any prompt files that have changed on disk since last load."""
        prompts_dir = Path(__file__).parent
        changed = False
        for filename in self.PROMPT_FILES:
            path = prompts_dir / filename
            if not path.exists():
                continue
            mtime = os.path.getmtime(path)
            if mtime != self._mtimes.get(filename):
                try:
                    self._file_cache[filename] = path.read_text(encoding="utf-8")
                    self._mtimes[filename] = mtime
                    changed = True
                    logger.info("Prompt hot-reloaded: %s", filename)
                except Exception:
                    logger.error("Failed to hot-reload %s", filename, exc_info=True)
        if changed:
            self._full_prompt = "\n\n".join(
                self._file_cache[f] for f in self.PROMPT_FILES if f in self._file_cache
            )
            logger.info("Prompt cache rebuilt after hot-reload")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        """Full prompt — all sections combined."""
        self._check_reload()
        return self._full_prompt

    def get_prompt_for_protocols(self, protocols: list[str]) -> str:
        """
        Return a minimal prompt containing only the sections needed for the
        given protocol list.  Always includes the base personality file.

        If protocols is empty, returns the full prompt.
        """
        self._check_reload()
        if not protocols:
            return self._full_prompt

        files_needed: set[str] = set(_ALWAYS_FILES)

        for proto in protocols:
            key = proto.lower().replace("-", "_").replace(" ", "_")
            mapped = PROTOCOL_FILE_MAP.get(key)
            if mapped:
                files_needed.update(mapped)
            else:
                # Unknown protocol — include core + queries as safe fallback
                files_needed.update(_FALLBACK_FILES)
                logger.debug("Unknown protocol '%s', using fallback files", proto)

        # Build in canonical order
        parts = [
            self._file_cache[f]
            for f in self.PROMPT_FILES
            if f in files_needed and f in self._file_cache
        ]
        prompt = "\n\n".join(parts)
        logger.debug(
            "Protocol prompt built: protocols=%s files=%s size=%.1f KB",
            protocols,
            [f for f in self.PROMPT_FILES if f in files_needed],
            len(prompt) / 1024,
        )
        return prompt

    @property
    def prompt_length(self) -> int:
        return len(self._full_prompt)

    @property
    def is_loaded(self) -> bool:
        return bool(self._full_prompt)


def get_prompt_loader() -> PromptLoader:
    return PromptLoader()


def get_system_prompt() -> str:
    return get_prompt_loader().get_system_prompt()

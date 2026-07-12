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
    # DEX / Swap / Lend — Jupiter is also a lending protocol (Jupiter Lend:
    # Earn + Borrow), so the lending fragment must load or "jupiter lend" has no
    # action grammar and the model falls back to a balance query.
    "jupiter":      ["solana_action_core.txt", "solana_action_dex.txt", "solana_action_lending.txt", "solana_action_market_data.txt"],
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
    # NFT marketplaces — Magic Eden read + trading; Tensor trading-routing.
    # market_data.txt carries the NFT composite (deep-dive) analysis.
    "tensor":       ["solana_action_nft.txt", "solana_action_market_data.txt"],
    "magic_eden":   ["solana_action_nft.txt", "solana_action_market_data.txt"],
    # pump.fun is its OWN fragment (not NFT). market_data.txt is included so
    # token_deep_analysis + all the analytics playbook load for pump tokens —
    # the most analysis/rug-heavy use case.
    "pumpfun":      ["solana_action_pumpfun.txt", "solana_action_market_data.txt"],
    # Cross-chain bridges
    "relay":        ["solana_action_crosschain.txt"],
    "debridge":     ["solana_action_crosschain.txt"],
    "squid":        ["solana_action_crosschain.txt"],
    # Token streaming / vesting
    "streamflow":   ["solana_action_streamflow.txt"],
    # Market data / analytics — open-ended wallet/token/NFT analysis intents
    "market_data":     ["solana_action_market_data.txt"],
    "birdeye":         ["solana_action_market_data.txt"],
    "helius":          ["solana_action_market_data.txt"],
    "dexscreener":     ["solana_action_market_data.txt"],
    "wallet":          ["solana_action_market_data.txt"],
    "wallet_analysis": ["solana_action_market_data.txt"],
    "portfolio":       ["solana_action_market_data.txt"],
    "token_analysis":  ["solana_action_market_data.txt"],
    "nft_analysis":    ["solana_action_nft.txt", "solana_action_market_data.txt"],
    "analytics":       ["solana_action_market_data.txt"],
    "analysis":        ["solana_action_market_data.txt"],
}

# Always loaded — personality, formatting, base action grammar.
# `solana_action_market_data.txt` is intentionally NOT here: it ships the
# heavy Nansen-style composite templates which would inflate the prompt
# for every trivial query. It is wired into PROTOCOL_FILE_MAP under all
# analytical-intent keys (wallet, wallet_analysis, portfolio, token_analysis,
# market_data, birdeye, helius, dexscreener, analytics, analysis,
# nft_analysis) so it loads only when an analytical request is detected,
# AND it sits in _FALLBACK_FILES so unrecognised intents that still ask
# for analysis are covered.
_ALWAYS_FILES = ["solana_action_base.txt"]

# Fallback when an unknown protocol is selected. Includes market_data so
# unmapped analytical questions ("just analyze this wallet for me") still
# reach the composite templates.
_FALLBACK_FILES = ["solana_action_core.txt", "solana_action_queries.txt", "solana_action_market_data.txt"]


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
        "solana_action_pumpfun.txt",
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

    def get_prompt_for_protocols(
        self,
        protocols: list[str],
        intent: str | None = None,
        is_chitchat: bool = False,
    ) -> str:
        """Return the smallest viable system prompt for this turn.

        The previous behaviour ("protocols=[] → return the FULL 252KB
        prompt") was the single largest source of wasted tokens: a "selam"
        with no protocols loaded all 12 prompt files (~63K tokens) even
        though the model needed maybe 500 tokens of identity + tone.

        New rules (cheapest to richest):
          • is_chitchat=True            → base file only (~7K tokens)
          • protocols=[] and advice/    → base file only (~7K tokens)
            ambiguous intent
          • protocols=[] and query      → base + queries + market_data
                                          (analytical fallback)
          • protocols=[<list>]          → base + protocol-specific files
        """
        self._check_reload()

        # 1. Chitchat — only the base personality. We deliberately drop
        # the action grammar files because the model is just saying hi.
        if is_chitchat:
            base = self._file_cache.get("solana_action_base.txt", "")
            return base

        # 2. No explicit protocol — branch by intent.
        if not protocols:
            files_needed: set[str] = set(_ALWAYS_FILES)
            if intent in (None, "advice", "ambiguous", "action"):
                # Light path: base only. The model still has function
                # calling (execute_action / query_onchain / clarify) at
                # the API level — it doesn't need 50KB of usage docs to
                # answer "what is staking" or to issue a tool call.
                pass
            elif intent == "query":
                # Live-data question without a named protocol: load the
                # query + market_data files so the model knows which
                # tools to dispatch.
                files_needed.update({"solana_action_queries.txt", "solana_action_market_data.txt"})
            else:
                files_needed.update(_FALLBACK_FILES)
        else:
            # 3. Explicit protocol list — load only those files.
            files_needed = set(_ALWAYS_FILES)
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
            "Protocol prompt built: protocols=%s intent=%s chitchat=%s files=%s size=%.1f KB",
            protocols, intent, is_chitchat,
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

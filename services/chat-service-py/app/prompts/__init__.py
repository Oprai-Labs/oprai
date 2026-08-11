"""
OPRAI Chat Service Prompts Package.

This package contains modular prompt files for the LLM system prompt.
The prompts are loaded via the loader module at application startup.

Modular Structure:
    - solana_action_base.txt      : Core personality, expertise, formatting
    - solana_action_queries.txt   : QUERY definitions (18 query types)
    - solana_action_core.txt     : Transfer, swap, stake, burn, etc.
    - solana_action_dex.txt      : Jupiter, Raydium, Orca, Meteora
    - solana_action_lending.txt  : Kamino, Jupiter Lend, Solend
    - solana_action_staking.txt  : Marinade, Jito
    - solana_action_nft.txt     : Tensor, Magic Eden, PumpFun
    - solana_action_crosschain.txt: Bridge, Wormhole, Relay

Performance:
    - Loaded once at startup via singleton PromptLoader
    - Cached in memory (~61KB)
    - ~0.2μs access time per request

Usage:
    from app.prompts import get_system_prompt
    prompt = get_system_prompt()
"""

from app.prompts.loader import get_prompt_loader, get_system_prompt

__all__ = ["get_prompt_loader", "get_system_prompt"]

# Initialize prompts at import time (when app starts)
# This ensures prompts are cached in memory before any request comes in
# The underscore prefix indicates it's for initialization only
_loader = get_prompt_loader()

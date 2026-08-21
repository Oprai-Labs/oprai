"""
Pre-compute facts for the LLM before it builds its tool plan.

Why
---
Small models (gpt-5.4-mini, claude-haiku-4-5) reliably FAIL at these patterns:

1. **Tool chaining**: call `sns_resolve("toly.sol")` → ignore the `owner` field
   in the result → pass `"toly.sol"` to `birdeye_wallet_portfolio` → 400 →
   refuse the question.
2. **Trivial data they could answer if they had it**: "what's my USDC
   balance" gets re-fetched from chain via a tool call even though the
   wallet block already contains the number.
3. **Comparisons**: "Marinade vs Jito APY" → model picks one DeFi tool,
   gets one side, refuses the comparison.

The pattern that works: compute the fact server-side in plain Python,
inject it as a system-message fact ("[Resolved: toly.sol → 86xC…]"), let
the LLM do the prose / action it's good at.

Pattern is intentionally cheap: regex detection on user message → at most
3 parallel fetches → short system message. No LLM in the loop.

Each detector returns `list[str]` of system-message bodies. The orchestrator
joins them into one `[Pre-computed facts]` block injected at the END of the
system stack so recency keeps them in attention.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from app.clients import market_data as md

logger = logging.getLogger(__name__)


# ── Patterns ────────────────────────────────────────────────────────────────
# Domains end in .sol; the SNS registry resolves a-z0-9-_ before the suffix.
_SNS_DOMAIN_RE = re.compile(r"\b([a-z0-9][a-z0-9_-]{0,63})\.sol\b", re.IGNORECASE)

# Price, balance and comparison intent all come from the intent classifier,
# which reads the message in whatever language it was written. They used to be
# keyword regexes here, which meant every new phrasing — and every new
# language — was a code change nobody remembered to make.


@dataclass(frozen=True)
class _Symbol:
    """A token symbol recognised in the user message, with its canonical mint."""
    symbol: str
    mint: str


def _scan_symbols(msg: str, limit: int = 3) -> list[_Symbol]:
    """Return registered token symbols mentioned in the user message.

    Word-boundary case-insensitive against the verified registry. Stops at
    `limit` to keep parallel fetches bounded. Skips sub-3-char symbols to
    avoid false positives ("AI" inside "fail").
    """
    try:
        from app.services.tokens_generated import VERIFIED_TOKENS
    except Exception:
        return []

    msg_lc = msg.lower()
    out: list[_Symbol] = []
    seen: set[str] = set()
    for t in VERIFIED_TOKENS:
        sym = str(t.get("symbol") or "")
        mint = str(t.get("address") or "")
        if not sym or not mint or len(sym) < 3 or sym in seen:
            continue
        if re.search(rf"\b{re.escape(sym.lower())}\b", msg_lc):
            out.append(_Symbol(sym, mint))
            seen.add(sym)
            if len(out) >= limit:
                break
    return out


# ── Detectors ──────────────────────────────────────────────────────────────


async def _facts_sns(msg: str) -> list[str]:
    """Resolve every *.sol domain mentioned in the message."""
    matches = _SNS_DOMAIN_RE.findall(msg)
    # Drop trivial false positives — file names like "config.sol".
    domains = [d for d in dict.fromkeys(matches) if not d.lower().endswith(("config", "test"))]
    if not domains:
        return []
    out: list[str] = []
    for d in domains[:3]:
        try:
            res = await md.sns_resolve(d)
            owner = res.get("owner")
            if owner:
                out.append(
                    f"[Resolved domain] {d}.sol → owner address `{owner}`. "
                    f"This is now the address you MUST use whenever you "
                    f"would otherwise pass `{d}.sol`:\n"
                    f"  • For transfer / send actions: `recipient` = `{owner}`. "
                    f"Emit execute_action(transfer) — do NOT refuse, do NOT "
                    f"say you cannot start the transaction; the address IS "
                    f"resolved.\n"
                    f"  • For wallet analysis / portfolio / PnL tools: "
                    f"`wallet` = `{owner}`.\n"
                    f"NEVER pass the literal string `{d}.sol` to any tool — "
                    f"that always 400s upstream."
                )
            else:
                out.append(
                    f"[Resolved domain] {d}.sol → not registered. Do not "
                    f"call wallet tools for this name; tell the user the "
                    f"domain has no owner."
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("pre_compute sns_resolve(%s) failed: %s", d, exc)
    return out


async def _facts_price(msg: str, wants_price: bool) -> list[str]:
    """Pre-fetch live price for any known token symbols when intent is pricing."""
    if not wants_price:
        return []
    symbols = _scan_symbols(msg)
    if not symbols:
        return []
    try:
        mints_csv = ",".join(s.mint for s in symbols)
        prices = await md.jup_price(mints_csv)
        rows: list[str] = []
        if isinstance(prices, dict):
            for s in symbols:
                row = prices.get(s.mint)
                if isinstance(row, dict):
                    price = row.get("usdPrice") or row.get("price")
                    if price is not None:
                        rows.append(f"  {s.symbol}: ${price}")
        if rows:
            return [
                "[Current prices — Jupiter, fresh] Use these directly. Do "
                "NOT re-fetch.\n" + "\n".join(rows)
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("pre_compute jup_price failed: %s", exc)
    return []


def _facts_balance(
    msg: str, wallet_balances: dict[str, float] | None, wants_balance: bool
) -> list[str]:
    """Surface user-owned token balances when the user asks 'how much X'.

    Pure compute — no I/O. The balances were already loaded into wallet
    context upstream; we just project them back out as a per-token line
    when the model is likely to need them.
    """
    if not wallet_balances or not wants_balance:
        return []
    symbols = _scan_symbols(msg)
    if not symbols:
        return []
    rows: list[str] = []
    for s in symbols:
        bal = wallet_balances.get(s.symbol) or wallet_balances.get(s.symbol.upper())
        if bal is None:
            # Try mint-keyed lookup (some upstream code keys by mint)
            bal = wallet_balances.get(s.mint)
        if bal is not None:
            rows.append(f"  {s.symbol}: {bal}")
    if not rows:
        return []
    return [
        "[Wallet balances — already loaded, do NOT re-fetch]\n" + "\n".join(rows)
    ]


async def _facts_compare(msg: str, compare_tokens: tuple[str, ...] = ()) -> list[str]:
    """For A vs B between known tokens, fetch both prices in parallel.

    Protocol-level comparisons (Marinade vs Jito) are not handled here —
    those need DeFiLlama / protocol-specific fetches that would be a
    separate detector with its own cost / accuracy tradeoffs. The token
    case is the common one and a single batched price call covers it.
    """
    if len(compare_tokens) < 2:
        return []
    # Re-scan with the token registry over the full message rather than
    # canonicalising the classifier's raw symbols (which could be "msol" /
    # "MSOL" / "Marinade") — keeps matching consistent with the other
    # detectors.
    symbols = _scan_symbols(msg, limit=4)
    if len(symbols) < 2:
        return []
    try:
        mints_csv = ",".join(s.mint for s in symbols[:4])
        prices = await md.jup_price(mints_csv)
        rows: list[str] = []
        if isinstance(prices, dict):
            for s in symbols[:4]:
                row = prices.get(s.mint)
                if isinstance(row, dict):
                    p = row.get("usdPrice") or row.get("price")
                    if p is not None:
                        rows.append(f"  {s.symbol}: ${p}")
        if rows:
            return [
                "[Comparison facts — token prices for the symbols the user "
                "is comparing. Use these for any price-driven answer.]\n"
                + "\n".join(rows)
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("pre_compute compare prices failed: %s", exc)
    return []


# ── Action-shape inference (model-failure backstop) ──────────────────────
#
# When the model emits `execute_action(swap, {})` with no params (a
# documented mini failure mode under tool_choice="required"), the
# user-visible result is an empty / broken card. The schema validator
# now rejects it via required-param checks, so the action gets dropped
# and the recovery loop fires — but the recovery often fails the same
# way for the same reason.
#
# This is a deterministic backstop: parse the user message with regex,
# extract amount + tokens + (optional) destination, and inject as a
# "[Inferred action]" system message. The recovery loop sees the
# pre-filled params and can satisfy `tool_choice=required` with a real
# action call.

_SWAP_RE = re.compile(
    r"\b(?:swap|convert|trade|exchange)\s+"
    r"(?P<amount>\d+(?:\.\d+)?|all)\s+"
    r"(?P<src>[A-Za-z][\w.-]{1,20})"
    r"\s+(?:to|for|into|->|→)\s+"
    r"(?P<dst>[A-Za-z][\w.-]{1,20}|[1-9A-HJ-NP-Za-km-z]{32,44})"
    r"\b",
    re.IGNORECASE,
)
_BUY_RE = re.compile(
    r"\bbuy\s+"
    r"(?P<amount>\d+(?:\.\d+)?)\s+"
    r"(?P<dst>[A-Za-z][\w.-]{1,20})"
    r"\s+(?:with|using|for)\s+"
    r"(?P<src>[A-Za-z][\w.-]{1,20})"
    r"\b",
    re.IGNORECASE,
)
# Stake intents. Three things identify one: the word "stake", a quantity of
# SOL, and a protocol name. Only the order varies, and the connector between
# them is whatever the user's language uses — an English "with", a Turkish
# "ile", a Spanish "con". Listing connectors is what made the old version
# English-plus-Turkish and nothing else, so the connector is simply skipped.
# "stake" itself is a loanword used untranslated across languages, and the
# protocol names are proper nouns, so neither needs a per-language variant.
_STAKE_RE = re.compile(
    r"\bstake\b.{0,30}?"
    r"(?P<amount1>\d+(?:\.\d+)?|all)\s+(?P<token1>SOL)\b.{0,30}?"
    r"(?P<protocol1>jito|marinade|jupiter|jupsol|sanctum)\b"
    r"|"
    r"(?P<amount2>\d+(?:\.\d+)?|all)\s+(?P<token2>SOL)\b.{0,30}?"
    r"(?P<protocol2>jito|marinade|jupiter|jupsol|sanctum)\b.{0,30}?\bstake\b"
    r"|"
    r"(?P<protocol3>jito|marinade|jupiter|jupsol|sanctum)\b.{0,30}?"
    r"(?P<amount3>\d+(?:\.\d+)?|all)\s+(?P<token3>SOL)\b.{0,30}?\bstake\b",
    re.IGNORECASE | re.DOTALL,
)

# Unstake intents — symbol prefix tells us which protocol.
# "unstake N mSOL" → marinade_unstake.  "unstake N jitoSOL" → jito_unstake.
_UNSTAKE_RE = re.compile(
    r"\b(?:unstake|withdraw\s+stake|unstaking)\s+"
    r"(?P<amount>\d+(?:\.\d+)?|all)\s+"
    r"(?P<symbol>mSOL|jitoSOL|JupSOL|INF|stSOL)",
    re.IGNORECASE,
)

# Lend / deposit-to-lending — "lend N USDC to kamino", "deposit N to solend".
_LEND_RE = re.compile(
    r"\b(?:lend|deposit|supply)\s+"
    r"(?P<amount>\d+(?:\.\d+)?|all)\s+"
    r"(?P<token>[A-Za-z][\w.-]{1,20})\s+"
    r"(?:to|on|via|with|into)\s+"
    r"(?P<protocol>kamino|solend)",
    re.IGNORECASE,
)

# Borrow — "borrow N USDC from kamino".
_BORROW_RE = re.compile(
    r"\bborrow\s+"
    r"(?P<amount>\d+(?:\.\d+)?|all)\s+"
    r"(?P<token>[A-Za-z][\w.-]{1,20})\s+"
    r"(?:from|on|via|using)\s+"
    r"(?P<protocol>kamino|solend)",
    re.IGNORECASE,
)


# Map protocol name → canonical action_type. Used by both inference paths.
_STAKE_PROTOCOL_MAP: dict[str, str] = {
    "jito": "jito_stake",
    "marinade": "marinade_stake",
    "jupiter": "jupsol_stake",
    "jupsol": "jupsol_stake",
    "sanctum": "marinade_stake",  # fallback — sanctum-specific action not exposed
}
_UNSTAKE_SYMBOL_MAP: dict[str, str] = {
    "msol": "marinade_unstake",
    "jitosol": "jito_unstake",
    "jupsol": "jupsol_unstake",
    "inf": "marinade_unstake",
    "stsol": "marinade_unstake",
}
_LEND_PROTOCOL_MAP: dict[str, str] = {
    "kamino": "kamino_deposit",
    "solend": "solend_deposit",
}
_BORROW_PROTOCOL_MAP: dict[str, str] = {
    "kamino": "kamino_borrow",
    "solend": "solend_borrow",
}


# Transfer shape, language-neutral: a quantity, a ticker, and a Solana address
# or .sol domain, with whatever the user's language puts between them. There is
# deliberately no verb in this pattern — requiring "send|transfer" is what made
# it English-only, and adding "gönder|yolla|envía|…" is the same bug wearing a
# longer list. The three things being extracted are recognisable without
# knowing the language; the address in particular is unmistakable.
_TRANSFER_RE = re.compile(
    r"(?P<amount>\d+(?:\.\d+)?|all)\s+"
    r"(?P<token>[A-Za-z][\w.-]{1,20})\b"
    r".{0,40}?\s"
    r"(?P<recipient>[1-9A-HJ-NP-Za-km-z]{32,44}|[a-z0-9_-]{1,63}\.sol)\b",
    re.IGNORECASE | re.DOTALL,
)


def _resolve_symbol_to_mint(token_ref: str) -> str | None:
    """Token ref → canonical mint. Returns the mint as-is if already 32-44 char base58."""
    if not token_ref:
        return None
    token_ref = token_ref.strip()
    # Already a Solana address? (loose base58 length check)
    if 32 <= len(token_ref) <= 44 and " " not in token_ref:
        return token_ref
    # Symbol lookup against the verified registry.
    try:
        from app.services.tokens_generated import VERIFIED_TOKENS
    except Exception:
        return None
    tref_lc = token_ref.lower()
    for t in VERIFIED_TOKENS:
        if str(t.get("symbol", "")).lower() == tref_lc:
            return str(t.get("address") or "")
    return None


async def resolve_sns_in_message(user_message: str) -> dict[str, str]:
    """Return `{".sol-name": owner_address}` for every domain in the user
    message. Cached at the `sns_resolve` client layer — repeated calls
    within the same turn are O(1). Returns an empty dict when no domain
    is mentioned or no domain resolves.
    """
    out: dict[str, str] = {}
    if not user_message:
        return out
    domains = _SNS_DOMAIN_RE.findall(user_message)
    if not domains:
        return out
    for d in list(dict.fromkeys(domains))[:3]:
        try:
            res = await md.sns_resolve(d)
            owner = res.get("owner")
            if owner:
                out[f"{d}.sol"] = owner
        except Exception as exc:  # noqa: BLE001
            logger.debug("resolve_sns_in_message(%s) failed: %s", d, exc)
    return out


def infer_action_params(user_message: str) -> dict:
    """Pure-Python action-param extraction. Returns
    `{"action_type": "swap", "params": {...}}` or `{}` if no match.

    Used as a server-side gap-fill at the validation step: when the model
    emits `execute_action({action_type: swap, params: {}})`, we merge
    these inferred params in before validation. The model's emitted
    params win on conflict; we only fill gaps.
    """
    msg_clean = (user_message or "").strip()
    if not msg_clean:
        return {}

    # Swap shape (covers swap/convert/trade/exchange "N TOKEN to TOKEN").
    m = _SWAP_RE.search(msg_clean)
    if m:
        amount = m.group("amount")
        src_mint = _resolve_symbol_to_mint(m.group("src"))
        dst_mint = _resolve_symbol_to_mint(m.group("dst"))
        if src_mint and dst_mint and amount:
            return {
                "action_type": "swap",
                "params": {
                    "inputMint": src_mint,
                    "outputMint": dst_mint,
                    "amount": amount,
                    "swapMode": "ExactIn",
                },
            }

    # Buy shape: "buy N TOKEN with TOKEN" — also routes to swap.
    m = _BUY_RE.search(msg_clean)
    if m:
        amount = m.group("amount")
        src_mint = _resolve_symbol_to_mint(m.group("src"))
        dst_mint = _resolve_symbol_to_mint(m.group("dst"))
        if src_mint and dst_mint and amount:
            return {
                "action_type": "swap",
                "params": {
                    "inputMint": src_mint,
                    "outputMint": dst_mint,
                    "amount": amount,
                    "swapMode": "ExactIn",
                },
            }

    # Transfer — language-neutral: quantity, ticker, address.
    m = _TRANSFER_RE.search(msg_clean)
    if m:
        amount = m.group("amount")
        token_mint = _resolve_symbol_to_mint(m.group("token"))
        recipient = m.group("recipient")
        if amount and token_mint and recipient:
            return {
                "action_type": "transfer",
                "params": {
                    "amount": amount,
                    "token": token_mint,
                    "recipient": recipient,
                },
            }

    # Stake — protocol can appear in 3 different positions.
    m = _STAKE_RE.search(msg_clean)
    if m:
        amount = m.group("amount1") or m.group("amount2") or m.group("amount3")
        proto = (m.group("protocol1") or m.group("protocol2")
                 or m.group("protocol3") or "").lower()
        action_type = _STAKE_PROTOCOL_MAP.get(proto)
        if amount and action_type:
            return {"action_type": action_type, "params": {"amount": amount}}

    # Unstake — symbol tells us which protocol.
    m = _UNSTAKE_RE.search(msg_clean)
    if m:
        amount = m.group("amount")
        sym = m.group("symbol").lower()
        action_type = _UNSTAKE_SYMBOL_MAP.get(sym)
        if amount and action_type:
            return {"action_type": action_type, "params": {"amount": amount}}

    # Lend / deposit-to-lending — protocol-keyed.
    m = _LEND_RE.search(msg_clean)
    if m:
        amount = m.group("amount")
        token_mint = _resolve_symbol_to_mint(m.group("token"))
        proto = m.group("protocol").lower()
        action_type = _LEND_PROTOCOL_MAP.get(proto)
        if amount and token_mint and action_type:
            return {
                "action_type": action_type,
                "params": {"amount": amount, "token": token_mint},
            }

    # Borrow — protocol-keyed.
    m = _BORROW_RE.search(msg_clean)
    if m:
        amount = m.group("amount")
        token_mint = _resolve_symbol_to_mint(m.group("token"))
        proto = m.group("protocol").lower()
        action_type = _BORROW_PROTOCOL_MAP.get(proto)
        if amount and token_mint and action_type:
            return {
                "action_type": action_type,
                "params": {"amount": amount, "token": token_mint},
            }

    return {}


def _infer_action_facts(msg: str) -> list[str]:
    """Detect action intents in the user message and emit a pre-filled
    parameter block. The recovery loop / model uses these as the source
    of truth when its own argument generation comes up empty.
    """
    out: list[str] = []
    msg_clean = msg.strip()

    # Swap shapes: "swap 0.1 SOL to USDC", "trade 5 USDC for BONK".
    m = _SWAP_RE.search(msg_clean)
    if m:
        amount = m.group("amount")
        src_mint = _resolve_symbol_to_mint(m.group("src"))
        dst_mint = _resolve_symbol_to_mint(m.group("dst"))
        if src_mint and dst_mint and amount:
            out.append(
                f"[Inferred action — swap]\n"
                f"User wants to swap. If you call execute_action, use these exact params:\n"
                f"  action_type: \"swap\"\n"
                f"  inputMint:   \"{src_mint}\"  ({m.group('src').upper()})\n"
                f"  outputMint:  \"{dst_mint}\"  ({m.group('dst').upper()})\n"
                f"  amount:      \"{amount}\"\n"
                f"  swapMode:    \"ExactIn\"\n"
                f"Emit execute_action with these params — DO NOT leave params empty."
            )

    # Buy shapes: "buy 5 USDC with SOL".
    m = _BUY_RE.search(msg_clean)
    if m and not out:
        amount = m.group("amount")
        src_mint = _resolve_symbol_to_mint(m.group("src"))
        dst_mint = _resolve_symbol_to_mint(m.group("dst"))
        if src_mint and dst_mint and amount:
            out.append(
                f"[Inferred action — swap (buy shape)]\n"
                f"User said 'buy {m.group('dst').upper()} with {amount} {m.group('src').upper()}'.\n"
                f"That is a swap. Params:\n"
                f"  action_type: \"swap\"\n"
                f"  inputMint:   \"{src_mint}\"  ({m.group('src').upper()})\n"
                f"  outputMint:  \"{dst_mint}\"  ({m.group('dst').upper()})\n"
                f"  amount:      \"{amount}\"\n"
                f"  swapMode:    \"ExactIn\""
            )

    # Transfer shape: a quantity, a ticker and an address, whatever sits
    # between them.
    m = _TRANSFER_RE.search(msg_clean)
    if m and not out:
        amount = m.group("amount")
        token_mint = _resolve_symbol_to_mint(m.group("token"))
        recipient = m.group("recipient")
        if amount and token_mint:
            out.append(
                f"[Inferred action — transfer]\n"
                f"User wants to transfer. Params:\n"
                f"  action_type: \"transfer\"\n"
                f"  amount:      \"{amount}\"\n"
                f"  token:       \"{token_mint}\"  ({m.group('token').upper()})\n"
                f"  recipient:   \"{recipient}\"\n"
                f"Note: if recipient ends in `.sol`, the resolved owner address has been "
                f"injected separately above — use that owner, not the .sol literal."
            )

    return out


# ── Deterministic fallback renderers ──────────────────────────────────────
#
# When the LLM fails to produce a useful response (empty stream, all tool
# calls dropped, recovery loop returned nothing), we render an answer
# server-side using the same data pre_compute already fetched. The point
# is: user ALWAYS sees a real answer for common questions — no "Sorry,
# couldn't generate" fallback, no hallucinated numbers.
#
# Each renderer returns plain markdown prose, or None if it can't handle
# the question. The caller tries them in order and uses the first hit.


async def render_price_prose(user_message: str, wants_price: bool = False) -> str | None:
    """If the user asked for a token price, fetch + render.

    Returns plain markdown text or None when no price intent / no
    recognised symbol. Uses Jupiter's price endpoint — real number, never
    fabricated. Language-agnostic output (just the symbol + dollar value).
    """
    if not wants_price:
        return None
    symbols = _scan_symbols(user_message)
    if not symbols:
        return None
    try:
        prices = await md.jup_price(",".join(s.mint for s in symbols))
    except Exception as exc:  # noqa: BLE001
        logger.warning("render_price_prose: jup_price failed: %s", exc)
        return None

    if not isinstance(prices, dict):
        return None
    rows: list[str] = []
    for s in symbols:
        row = prices.get(s.mint)
        if isinstance(row, dict):
            price = row.get("usdPrice") or row.get("price")
            if price is not None:
                try:
                    p = float(price)
                except (TypeError, ValueError):
                    continue
                # Format: 4 decimal places for sub-$1 tokens, 2 otherwise.
                price_str = f"${p:,.4f}" if p < 1 else f"${p:,.2f}"
                rows.append(f"- **{s.symbol}** — {price_str}")
    if not rows:
        return None
    if len(rows) == 1:
        # Single-token query — natural sentence.
        return rows[0].lstrip("- ").replace("**", "")  # "SOL — $185.20"
    return "Current prices:\n\n" + "\n".join(rows)


def render_balance_prose(
    user_message: str,
    wallet_balances: dict[str, float] | None,
    wants_balance: bool = False,
) -> str | None:
    """If the user asked for a token balance, render from cached balances.

    Pure compute — no I/O. Returns None when no balance intent or no
    recognised symbol.
    """
    if not wants_balance or not wallet_balances:
        return None
    symbols = _scan_symbols(user_message)
    if not symbols:
        return None
    rows: list[str] = []
    for s in symbols:
        bal = (
            wallet_balances.get(s.symbol)
            or wallet_balances.get(s.symbol.upper())
            or wallet_balances.get(s.mint)
        )
        if bal is not None:
            # Format: trim trailing zeros, max 6 decimals.
            try:
                bal_str = f"{float(bal):,.6f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                bal_str = str(bal)
            rows.append(f"- **{s.symbol}** — {bal_str}")
        else:
            rows.append(f"- **{s.symbol}** — 0 (not in wallet)")
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0].lstrip("- ").replace("**", "")
    return "Balances:\n\n" + "\n".join(rows)


async def render_category_prose(
    user_message: str, token_category: str | None = None
) -> str | None:
    """If the user asked a category-listing question (stables/LST/memecoin/
    blue-chip), render the canonical list directly.

    The category comes from the intent classifier, which reads the message in
    whatever language it was written. Without one there is nothing to render —
    guessing from keywords is what this used to do, and it only ever knew the
    words someone had thought to list.
    """
    if not token_category:
        return None
    try:
        from app.services.token_categories import get_category_tokens
    except Exception:
        return None
    cat = token_category
    tokens = await get_category_tokens(cat)
    if not tokens:
        return None
    pretty = {"stable": "stablecoins", "lst": "liquid staking tokens",
              "blue_chip": "blue-chip tokens", "memecoin": "memecoins"}.get(cat, cat)
    lines = [f"Here are the canonical Solana {pretty}:", ""]
    for t in tokens:
        lines.append(f"- **{t.symbol}** — {t.role}")
    return "\n".join(lines)


async def render_sns_prose(user_message: str) -> str | None:
    """If the user mentioned a `.sol` domain, resolve and report owner."""
    domains = _SNS_DOMAIN_RE.findall(user_message)
    if not domains:
        return None
    rows: list[str] = []
    # dict.fromkeys gives an ordered-dedup; list() lets us slice (dict
    # views are not sliceable on Python 3.14+).
    for d in list(dict.fromkeys(domains))[:3]:
        try:
            res = await md.sns_resolve(d)
            owner = res.get("owner")
            if owner:
                rows.append(f"- **{d}.sol** → `{owner}`")
            else:
                rows.append(f"- **{d}.sol** → not registered")
        except Exception as exc:  # noqa: BLE001
            logger.debug("render_sns_prose(%s) failed: %s", d, exc)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0].lstrip("- ").replace("**", "")
    return "Resolved:\n\n" + "\n".join(rows)


async def render_compare_prose(
    user_message: str, compare_tokens: tuple[str, ...] = ()
) -> str | None:
    """For 'compare A and B' between known tokens, fetch both prices and
    return a side-by-side prose. Mirrors _facts_compare but formatted for
    direct user display (used by the hedge-override path)."""
    if len(compare_tokens) < 2:
        return None
    # The symbols come from the classifier, verbatim as the user wrote them.
    # Keeping the unrecognised ones matters: a token missing from the local
    # registry is still named back to the user below, rather than silently
    # dropped or swapped for a familiar-looking one.
    raw_syms: list[str] = []
    for g in compare_tokens:
        if g and g.upper() not in raw_syms:
            raw_syms.append(g.upper())
    raw_syms = raw_syms[:4]

    symbols = _scan_symbols(user_message, limit=4)
    sym_by_upper = {s.symbol.upper(): s for s in symbols}

    # Try to price every captured symbol we have a mint for.
    mints_with_sym = [
        (sym, sym_by_upper[sym].mint) for sym in raw_syms if sym in sym_by_upper
    ]
    rows: list[str] = []
    if mints_with_sym:
        try:
            mints_csv = ",".join(mint for _, mint in mints_with_sym)
            prices = await md.jup_price(mints_csv)
            for sym, mint in mints_with_sym:
                row = (prices or {}).get(mint) if isinstance(prices, dict) else None
                if isinstance(row, dict):
                    p = row.get("usdPrice") or row.get("price")
                    if p is not None:
                        rows.append(f"- **{sym}**: ${p}")
                        continue
                rows.append(f"- **{sym}**: (price not available)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("render_compare_prose price fetch failed: %s", exc)

    # Append any captured symbols we couldn't find in the registry so the
    # user sees them named (e.g. newer stables not yet whitelisted).
    priced_upper = {sym for sym, _ in mints_with_sym}
    for sym in raw_syms:
        if sym not in priced_upper:
            rows.append(f"- **{sym}**: (token not in local registry)")

    if not rows:
        return None
    return (
        f"Comparing {' vs '.join(raw_syms)}:\n\n"
        + "\n".join(rows)
    )


async def render_fallback_prose(
    user_message: str,
    wallet_balances: dict[str, float] | None = None,
    token_category: str | None = None,
    wants_price: bool = False,
    wants_balance: bool = False,
    compare_tokens: tuple[str, ...] = (),
) -> str | None:
    """Try each renderer in priority order. Return the first hit.

    Used by message.py when the LLM stream is empty — guarantees the
    user sees a real, data-backed answer for the most common question
    types instead of an "I couldn't generate" fallback.

    Order: more-specific intents first so "balance of SOL" doesn't get
    answered as "price of SOL".
    """
    if not user_message:
        return None
    # Balance is sync (pure compute against cached wallet) — try first.
    try:
        r = render_balance_prose(user_message, wallet_balances, wants_balance)
        if r:
            return r
    except Exception:
        logger.debug("balance renderer failed", exc_info=True)
    # Async renderers — each fetches its own source.
    # Compare runs first so "compare A and B" is not answered as a single
    # token price.
    for fn, extra in (
        (render_compare_prose, (compare_tokens,)),
        (render_sns_prose, ()),
        (render_price_prose, (wants_price,)),
    ):
        try:
            r = await fn(user_message, *extra)
            if r:
                return r
        except Exception:
            logger.debug("renderer %s failed", fn.__name__, exc_info=True)
    try:
        r = await render_category_prose(user_message, token_category)
        if r:
            return r
    except Exception:
        logger.debug("renderer render_category_prose failed", exc_info=True)
    return None


# ── Orchestrator ───────────────────────────────────────────────────────────


async def precompute_facts(
    user_message: str,
    wallet_balances: dict[str, float] | None = None,
    wants_price: bool = False,
    wants_balance: bool = False,
    compare_tokens: tuple[str, ...] = (),
) -> str | None:
    """Run all detectors in parallel; return a single combined system block.

    `None` if nothing relevant. The returned string is intended to be
    appended as the LAST system message (highest recency) so the model
    treats the facts as fresh ground truth.
    """
    if not user_message:
        return None

    msg = user_message.strip()
    if not msg:
        return None

    sns_task = asyncio.create_task(_facts_sns(msg))
    price_task = asyncio.create_task(_facts_price(msg, wants_price))
    compare_task = asyncio.create_task(_facts_compare(msg, compare_tokens))

    # Sync (pure compute) — do inline.
    balance_facts = _facts_balance(msg, wallet_balances, wants_balance)
    action_facts = _infer_action_facts(msg)

    sns_facts, price_facts, compare_facts = await asyncio.gather(
        sns_task, price_task, compare_task, return_exceptions=False,
    )

    # Cross-link SNS resolution into the inferred-action block. When the
    # user wrote "0.5 USDC göndereyim toly.sol adresine", the transfer
    # inference initially fills `recipient: "toly.sol"` — but a `.sol`
    # name is not a Solana pubkey, the action would fail downstream.
    # SNS resolution already ran in parallel; if it found an owner, swap
    # the literal `.sol` in every inferred-action fact for the owner.
    _sns_map: dict[str, str] = {}
    for fact in sns_facts:
        m = re.search(r"\[Resolved domain\]\s+(\S+\.sol)\s+→\s+owner address `([1-9A-HJ-NP-Za-km-z]{32,44})`", fact)
        if m:
            _sns_map[m.group(1)] = m.group(2)
    if _sns_map and action_facts:
        rewritten: list[str] = []
        for fact in action_facts:
            for sol_name, owner in _sns_map.items():
                fact = fact.replace(f'"{sol_name}"', f'"{owner}"')
                # Also swap in the parenthetical comment to keep the
                # symbol→address mapping visible to the model.
                fact = fact.replace(sol_name, f"{owner} (from {sol_name})")
            rewritten.append(fact)
        action_facts = rewritten

    all_facts: list[str] = []
    # SNS first so the inferred-action transfer can reference the resolved owner.
    all_facts.extend(sns_facts)
    all_facts.extend(price_facts)
    all_facts.extend(balance_facts)
    all_facts.extend(compare_facts)
    # Action inference last → highest recency = most likely to be obeyed.
    all_facts.extend(action_facts)

    if not all_facts:
        return None

    header = (
        "[Pre-computed facts — authoritative, computed by server before this "
        "turn. Treat as ground truth. Do NOT call tools to verify these "
        "values; do NOT refuse the question claiming you lack the data.]"
    )
    body = "\n\n".join(all_facts)
    return f"{header}\n\n{body}"

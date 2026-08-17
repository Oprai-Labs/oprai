"""
Intent router — pre-classifies the user's message into one of 4 buckets so the
main LLM call gets a *constrained* tool set instead of all 200+ tools.

Why this exists
---------------
Haiku 4.5 is great at filling DeFi parameters but struggles to discriminate
between "execute an action" and "look up balance" when the verb is in a
language it parses weakly (Turkish "sat", Spanish "vender", etc.). With the
full tool list it tends to fall back to `query_onchain('balance', ...)` —
the safest-looking tool — which makes the user repeat themselves.

The fix is structural: a tiny classifier (gpt-4o-mini, ~100 ms, ~$0.00002 per
call, multilingual native) decides intent first. The downstream tool list is
then filtered so the main model can only pick from the tools that match the
intent. Balance fallback becomes physically impossible when balance isn't in
the tool list.

Failure mode: any classifier error → returns `ambiguous` so the existing
"send all tools, let the model decide" path runs (no regression).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

IntentClass = Literal["action", "query", "advice", "ambiguous"]

# Canonical protocol ids the classifier may emit. Anything outside this set
# is silently dropped from the result so a noisy LLM cannot inject arbitrary
# strings into the downstream tool/prompt scoping path.
VALID_PROTOCOLS: frozenset[str] = frozenset({
    "jupiter", "raydium", "orca", "meteora",
    "marinade", "jito", "native_stake",
    "kamino", "solend",
    "tensor", "magic_eden", "pumpfun",
    "relay", "debridge",
    "streamflow",
})


# Keyword augmentation: a deterministic safety net that force-adds a protocol
# ONLY when its unambiguous product name appears literally in the message —
# guarding a known failure mode where a substring match (e.g. "JupSOL" →
# "jupiter") makes the classifier miss the actually-named protocol.
#
# STRICT RULE (do not violate): every entry here is a single-meaning PRODUCT /
# PROTOCOL / FEATURE NAME (e.g. "meteora", "dlmm", "pumpswap", "wormhole").
# NEVER put generic verbs ("bridge", "swap"), chain names ("ethereum",
# "base"), or language-specific words ("köprü") here. Those carry MEANING, not
# a name — interpreting meaning (in any language) is the classifier's job
# (Katman 1 / gpt-5.4-nano), not a regex table. A word like "base" is also a
# common English noun and would misfire. Word-boundary matched, case-insensitive.
#
# Cross-chain (`relay`) therefore keeps only provider NAMES here (relay.link,
# wormhole, mayan). Generic cross-chain intent ("bridge my USDC to Arbitrum",
# in any language) is detected semantically by the classifier prompt, which
# maps any non-Solana chain / bridge intent to `relay`.
_PROTOCOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "meteora":   ("meteora", "dlmm", "damm", "m3m3", "stake2earn"),
    "raydium":   ("raydium", "clmm"),
    "orca":      ("orca", "whirlpool"),
    "jupiter":   ("jupiter", "juplend", "jup lend", "jup earn"),
    "kamino":    ("kamino", "klend", "k-lend", "kswap"),
    "jito":      ("jito",),
    "marinade":  ("marinade",),
    "pumpfun":   ("pumpfun", "pump.fun", "pumpswap", "mayhem"),
    "magic_eden":("magiceden", "magic eden", "mmm pool"),
    "tensor":    ("tensor", "tensorians"),
    "streamflow":("streamflow",),
    # Provider NAMES only. Generic cross-chain intent + chain names are handled
    # semantically by the classifier (see the cross-chain rule in the prompt).
    "relay":     ("relay.link", "relay bridge", "wormhole", "mayan"),
    "debridge":  ("debridge",),
}


# Token symbols that BELONG to a protocol without naming it. Deliberately not
# in the table above, because the two are not the same claim: "marinade" is the
# user choosing a venue, "mSOL" is the user naming an asset. Treating the
# second as the first is how a Raydium pool-selection flow, answered with the
# pair "SOL/mSOL", turned into a Marinade staking card — the token displaced
# the protocol the user was already inside, the tool list narrowed to Marinade,
# and the action they were halfway through stopped being offerable at all.
#
# These still route a cold start ("convert my SOL to mSOL" with no history),
# but only when nothing else has established a protocol.
_TOKEN_PROTOCOL_HINTS: dict[str, tuple[str, ...]] = {
    "marinade": ("msol",),
    "jito":     ("jitosol",),
    "jupiter":  ("jupsol",),
}


# Compiled regex cache for word-boundary matching. Built lazily on first use.
_KEYWORD_REGEX_CACHE: dict[str, re.Pattern[str]] = {}


def _kw_regex(kw: str) -> re.Pattern[str]:
    cached = _KEYWORD_REGEX_CACHE.get(kw)
    if cached is None:
        cached = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        _KEYWORD_REGEX_CACHE[kw] = cached
    return cached


def _augment_protocols_from_keywords(
    msg: str, protocols: tuple[str, ...]
) -> tuple[str, ...]:
    """Add protocols whose strong keywords appear in the message.

    Returns a sorted tuple containing both the classifier's picks and any
    protocols whose product-name keyword appears as a whole word. Idempotent —
    if the classifier already picked the protocol, this is a no-op.

    Word-boundary matched so "base" (Base chain) does not collide with
    "database"/"based"/"baseline".
    """
    if not msg:
        return protocols
    augmented = set(protocols)
    for proto, keywords in _PROTOCOL_KEYWORDS.items():
        if proto in augmented:
            continue
        if any(_kw_regex(kw).search(msg) for kw in keywords):
            augmented.add(proto)

    # Token hints are the weak signal and only speak when nobody else has:
    # naming an asset picks a venue only if no venue is on the table yet.
    if not augmented:
        for proto, symbols in _TOKEN_PROTOCOL_HINTS.items():
            if any(_kw_regex(sym).search(msg) for sym in symbols):
                augmented.add(proto)

    return tuple(sorted(augmented))


def named_protocols(msg: str) -> frozenset[str]:
    """Protocols whose product name appears *literally* in the message.

    This is the deterministic half of detection — it reflects only what the
    user actually typed, with none of the classifier's semantic inference. Use
    it when downstream code must distinguish "the user named protocol X" from
    "the classifier guessed X" (e.g. correcting a wrong-protocol action). Same
    centralized `_PROTOCOL_KEYWORDS` table and word-boundary matching as the
    augmentation net — never grow a parallel keyword list elsewhere.
    """
    if not msg:
        return frozenset()
    return frozenset(
        proto
        for proto, keywords in _PROTOCOL_KEYWORDS.items()
        if any(_kw_regex(kw).search(msg) for kw in keywords)
    )


def protocols_from_emitted_types(types: "list[str] | tuple[str, ...]") -> frozenset[str]:
    """Protocols implied by the action / query types a previous turn emitted.

    Types are named `<protocol>_<what>` (`raydium_open_position`,
    `meteora_dlmm_get_pairs`), so the prefix IS the protocol — no second
    keyword table, and a type that does not start with a known id (`balance`,
    `swap`) simply contributes nothing.

    Used to carry an established protocol into the next turn. The classifier
    sees the conversation and is supposed to keep it, and does not reliably:
    told "SOL/mSOL" inside a Raydium flow it answered `marinade`, dropping
    Raydium entirely — and since the tool list is built from what it returns,
    `raydium_open_position` was never offered to the model at all.
    """
    if not types:
        return frozenset()
    known = set(_PROTOCOL_KEYWORDS)
    found = set()
    for t in types:
        name = str(t or "")
        for proto in known:
            if name.startswith(proto + "_"):
                found.add(proto)
                break
    return frozenset(found)


@dataclass(frozen=True)
class IntentResult:
    intent: IntentClass
    confidence: float           # 0.0 – 1.0
    protocols: tuple[str, ...]  # canonical ids the user mentioned/implied; empty if none
    reason: str                 # short tag for logs ("model" / "fallback:err" / "cache")
    # True when the user is asking about a CATEGORY of tokens (stables, LSTs,
    # blue-chips, memecoins, wrapped) rather than a specific token or pair.
    # Drives a structural tool-filter that strips pool-list / token-list
    # queries — those return rows that contradict the category (a "what
    # stables exist on Raydium" answer that includes MEW/WSOL pools).
    is_category_request: bool = False


# ---------------------------------------------------------------------------
# Classifier prompt
# ---------------------------------------------------------------------------
# Kept short on purpose — every extra token is paid per request. The model only
# needs to discriminate between four buckets, not understand DeFi specifics.

_SYSTEM = """\
You are a routing classifier for a Solana DeFi chat agent. Read the user's
most recent message (in any language) plus a small amount of prior context,
and emit two pieces of information:

  1. ONE intent label.
  2. The set of Solana protocols / venues the user is talking about
     (canonical ids; multiple allowed; empty list when none).

Intents (use exactly these strings):

- "action": user wants to EXECUTE an on-chain transaction (sell, buy, swap,
  transfer, send, stake, lend, borrow, withdraw, deposit, mint, launch, close,
  add/remove liquidity, claim — in ANY language). Even if some parameters are
  missing, if the verb expresses doing-something-with-funds, this is "action".

- "query": user wants to LOOK AT live on-chain or market data — their own
  balance/holdings/portfolio, current token price, open positions, trending
  tokens, pool list, APY/TVL from a live feed. Pure data read — the answer
  requires fetching real-time numbers from an API or blockchain.

- "advice": user wants EXPLANATION, EDUCATION, or REASONING. This covers:
  • Conceptual questions: "what is X?", "how does X work?", "explain X",
    "what's the difference between X and Y?" — in any language, any phrasing.
  • Opinion / recommendation: "should I?", "is it wise?", "which is better?"
  • Comparison of two concepts or strategies without needing live numbers
  • Risk/benefit discussion, strategy analysis, DeFi education
  Key rule: if the answer comes from knowledge rather than a live data fetch,
  classify as "advice". Do NOT classify conceptual questions as "query" just
  because they mention a metric name (MVRV, UTXO, impermanent loss, AMM) —
  asking what MVRV means is education, not a data request.

- "ambiguous": cannot tell, or the message mixes multiple intents in a way
  the classifier shouldn't guess at.

Verb rule: any action verb ("sell", "buy", "swap", "send", "stake", etc.) in
imperative or first-person form is "action", regardless of language or how
short the message is. Do NOT classify it as "query" just because parameters
are unclear.

Protocols (canonical id list — use ONLY these strings, multiple allowed):

  jupiter      — Jupiter aggregator, JUP, JLP, jupSOL, Jupiter Lend, Jupiter Perps,
                 Jupiter DCA / limit orders.
  raydium      — Raydium AMM, CLMM, concentrated liquidity, AcceleRaytor.
  orca         — Orca Whirlpools, concentrated liquidity on Orca.
  meteora      — Meteora DLMM, DAMM v1/v2, Dynamic Vaults, Stake2Earn (m3m3),
                 mayhem mode.
  marinade     — Marinade liquid staking, mSOL, marinade-native.
  jito         — Jito staking, jitoSOL, MEV tips, Jito bundles.
  native_stake — Generic Solana validator stake (no LST), stake account ops.
  kamino       — Kamino K-Lend, K-Vault, K-Swap, Multiply, Long/Short, kpool.
  solend       — Solend (read-only; transactions deprecated).
  tensor       — Tensor NFT marketplace.
  magic_eden   — Magic Eden, ME, MMM pools.
  pumpfun      — pump.fun token launches, PumpSwap, mayhem.
  relay        — Relay.link cross-chain bridge.
  debridge     — deBridge cross-chain.
  streamflow   — Streamflow token streaming / vesting.

Detection rules:
- The user often names a venue or product without naming the protocol — map
  it back: "DLMM" / "DAMM" / "m3m3" / "stake2earn" → meteora; "Whirlpool" →
  orca; "CLMM" → raydium; "K-Lend" / "kvault" / "kswap" → kamino; "MMM" →
  magic_eden; "jupSOL" → jupiter; "mSOL" → marinade; "jitoSOL" → jito.
  Those token mappings hold when the TOKEN is the subject ("convert my SOL to
  mSOL"). They do not override an in-flight flow — see the next rule.
- An in-flight flow keeps its protocol. When earlier turns established one —
  the user asked for it, or you offered options belonging to it — the next
  message continues that flow unless the user names a DIFFERENT protocol. A
  message that only names tokens is ANSWERING the question you just asked, not
  starting a new subject: "SOL/mSOL" inside a pool-selection flow picks the
  pair, it does not request liquid staking. Keep the established protocol; add
  another only when the user actually named one.
  Dropping it is the expensive mistake, because the tool list narrows to
  whatever you emit here — so the action the user was in the middle of stops
  being offerable at all, and the model can only answer with a different
  protocol's action. Emitting both costs nothing by comparison.
- Cross-chain detection: this is a Solana-native app, so any mention of a
  non-Solana chain (Ethereum, Base, Arbitrum, Optimism, Polygon, BSC,
  Avalanche, Linea, Scroll, zkSync, Celo, Fantom, Polygon zkEVM, Arbitrum
  Nova) OR a bridge/cross-chain verb in any language ("bridge",
  "cross-chain", "köprüle", "köprü", "puente") implies cross-chain →
  emit "relay" in protocols. Relay.link is the default cross-chain
  provider; only emit "debridge" when the user names it
  explicitly. Wormhole and Mayan have no canonical id — when the user
  names them, still emit "relay" so the cross-chain prompt section loads
  (they route through `cross_chain_swap` inside that section).
- Multiple protocols in one message: include all of them. "swap on Raydium
  then deposit to Kamino" → ["raydium","kamino"]. "swap 1 SOL to ETH on
  Base" → ["relay"] (cross-chain dominates; same-chain DEX is not Jupiter
  here because the destination chain differs).
- Ambiguous protocol words (e.g. "lend") with no other signal: leave the
  protocol list empty rather than guessing.
- A token symbol alone (BONK, USDC, WIF, RAY, SOL …) is NOT a protocol;
  emit empty protocols unless the message also names a venue.

Category-request detection (boolean field `is_category_request`):
- TRUE only for a STATIC taxonomic class — a fixed set you could answer
  from a stored list without a live lookup: "which stables", "what LSTs",
  "list blue chips", "what wrapped assets". Recognise the intent in any
  language (Turkish, Spanish, German, etc.) — match by semantics, not by a
  literal keyword.
- FALSE for a RANKED or TIME-ORDERED live feed even when no specific token
  is named — trending, newest / just-launched, top gainers or losers, most
  active, highest volume, or items crossing a lifecycle threshold ("about
  to graduate", "king of the hill"). These are live data served by a
  dedicated tool, not a static class, so they must keep their tools.
- FALSE when the user names a specific token or pair: "buy 5 USDC",
  "USDS/USDC pool", "swap SOL to JLP", "show jitoSOL stats".
- FALSE when the user asks about NON-category data: balance, transaction
  history, current price of a named token, validator list, pool list for a
  named pair.
- FALSE when the user asks for POOLS, pairs, markets, farms or vaults of a
  class rather than for the class itself: "stablecoin pools", "list the RWA
  pools", "meme pools on Orca", "stablecoin havuzlarını listele". A class of
  TOKENS is a static list; a class of POOLS is live market data with TVL and
  fees, and the pool tools filter by category themselves. The discriminator
  is whether the user named a venue (pool / pair / market / farm / vault) in
  any language — if they did, this is FALSE.
- FALSE for a protocol's own configuration, global state, or on-chain
  parameters (fee settings, program limits, a global/config account). That is
  a single live record fetched by a dedicated tool, not a static class of
  tokens — it must keep its tool. "Category" means a CLASS OF TOKENS, never a
  protocol's settings.
The downstream router uses this flag to drop pool-list / token-list
tools so a "what stables exist on Raydium" question never mounts a pool
mini-app with non-stable rows in it.

Respond with ONLY a single-line JSON object, no other text:
{"intent":"<one of the four>", "confidence": <0.0-1.0>, "protocols": ["..."], "is_category_request": <true|false>}\
"""


# ---------------------------------------------------------------------------
# Lightweight in-process cache (process-local; restarts on reload)
# ---------------------------------------------------------------------------
# Multiple identical messages within the same browser tab (retry, refresh,
# duplicate send) shouldn't hit the API twice.

_CACHE: dict[str, tuple[float, IntentResult]] = {}
_CACHE_TTL_S = 300.0  # 5 min
_CACHE_MAX = 1024


def _cache_key(message: str, context: str) -> str:
    h = hashlib.sha256()
    h.update(message.encode("utf-8"))
    h.update(b"\n--\n")
    h.update(context.encode("utf-8"))
    return h.hexdigest()


def _cache_get(key: str) -> IntentResult | None:
    now = time.time()
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, value = entry
    if now - ts > _CACHE_TTL_S:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: IntentResult) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # Cheap eviction: drop oldest entry.
        oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class IntentRouter:
    """Singleton classifier. Lazy-builds the OpenAI client on first use."""

    _instance: "IntentRouter | None" = None

    def __new__(cls) -> "IntentRouter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._client: AsyncOpenAI | None = None
        # Default model: gpt-5.4-nano (rest of the codebase already uses it).
        # Reasoning models need `max_completion_tokens` and tolerate a higher
        # cap because the reasoning trace counts against it; the request path
        # below handles both reasoning and classic chat models.
        self._model = getattr(settings, "OPRAI_INTENT_CLASSIFIER_MODEL", None) or "gpt-5.4-nano"
        self._enabled = bool(settings.OPRAI_OPENAI_API_KEY)
        if not self._enabled:
            logger.warning(
                "IntentRouter disabled: OPRAI_OPENAI_API_KEY is empty. "
                "Falling back to ambiguous on every request."
            )

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)
        return self._client

    def _record_usage(self, resp, wallet: str, session_id: str | None) -> None:
        """Fire-and-forget the classifier's token usage into the LLM ledger.

        Never raises and never blocks classification — a ledger miss must not
        cost us the routing decision.
        """
        if not wallet:
            return
        try:
            u = getattr(resp, "usage", None)
            if u is None:
                return
            prompt = int(getattr(u, "prompt_tokens", 0) or 0)
            completion = int(getattr(u, "completion_tokens", 0) or 0)
            cached = 0
            details = getattr(u, "prompt_tokens_details", None)
            if details is not None:
                cached = int(getattr(details, "cached_tokens", 0) or 0)
            fresh = max(0, prompt - cached)  # OpenAI prompt_tokens includes cache reads
            if fresh + completion <= 0:
                return
            from app.services.usage_ledger import record_llm_usage

            asyncio.create_task(
                record_llm_usage(
                    wallet=wallet,
                    session_id=session_id,
                    model=self._model,
                    request_kind="intent",
                    prompt_tokens=fresh,
                    completion_tokens=completion,
                    cached_tokens=cached,
                    is_estimated=False,
                )
            )
        except Exception:  # pragma: no cover — telemetry must never break routing
            logger.debug("intent classifier usage record failed", exc_info=True)

    async def classify(
        self,
        user_message: str,
        recent_context: str = "",
        timeout_s: float = 4.0,
        wallet: str = "",
        session_id: str | None = None,
    ) -> IntentResult:
        """
        Classify *user_message*. *recent_context* should be a short string
        with the last assistant turn(s) so referential phrases like "all of it"
        can be resolved even in non-English messages.

        Always returns a valid IntentResult; never raises.
        """
        msg = (user_message or "").strip()
        if not msg:
            return IntentResult("ambiguous", 0.0, (), "empty")

        if not self._enabled:
            return IntentResult("ambiguous", 0.0, (), "disabled")

        key = _cache_key(msg, recent_context)
        cached = _cache_get(key)
        if cached is not None:
            return IntentResult(cached.intent, cached.confidence, cached.protocols, "cache")

        try:
            user_payload = msg
            if recent_context:
                user_payload = (
                    f"<recent_context>\n{recent_context}\n</recent_context>\n\n"
                    f"<user_message>\n{msg}\n</user_message>"
                )

            client = self._get_client()
            # Reasoning models (gpt-5.x family) reject `max_tokens` and require
            # `max_completion_tokens` — and they also need a much higher cap
            # because the reasoning trace counts against it. Older Chat models
            # (gpt-4o-mini, gpt-4*) use `max_tokens` and 40 is plenty for a
            # one-line JSON object.
            is_reasoning = self._model.startswith(("gpt-5", "o1", "o3", "o4"))
            kwargs: dict = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user_payload},
                ],
                "response_format": {"type": "json_object"},
                "timeout": timeout_s,
            }
            if is_reasoning:
                # Reasoning trace can eat a few hundred tokens; the JSON answer
                # itself is ~50 (now includes protocols). 1024 keeps cost trivial.
                kwargs["max_completion_tokens"] = 1024
            else:
                # 40 was tight when the schema was just intent/confidence.
                # With the protocols list a malicious or chatty model could
                # spill past 40 tokens before closing the JSON; 120 still
                # rounds to ~$0.00003/call at gpt-4o-mini rates.
                kwargs["max_tokens"] = 120
                # Reasoning models reject custom temperature; only set for
                # classic chat models where determinism matters for caching.
                kwargs["temperature"] = 0.0
            resp = await client.chat.completions.create(**kwargs)

            # Record this classifier call's token cost (fire-and-forget). Only
            # the real API path reaches here — the cache short-circuit above is
            # free and correctly records nothing. OpenAI's prompt_tokens INCLUDES
            # cached, so normalise to fresh-billable + cached-read the same way
            # the responder does.
            self._record_usage(resp, wallet, session_id)

            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)

            intent = data.get("intent", "ambiguous")
            if intent not in ("action", "query", "advice", "ambiguous"):
                intent = "ambiguous"
            try:
                confidence = float(data.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))

            # Coerce protocols: keep only canonical ids, drop hallucinations.
            raw_protos = data.get("protocols") or []
            if not isinstance(raw_protos, list):
                raw_protos = []
            protocols = tuple(sorted({
                p.lower().replace("-", "_").strip()
                for p in raw_protos
                if isinstance(p, str)
                and p.lower().replace("-", "_").strip() in VALID_PROTOCOLS
            }))

            # Belt-and-braces: if the user mentioned a protocol by name (or a
            # protocol-specific term like "DLMM"/"CLMM"), force it in even
            # when the classifier missed it. Substring confusion in the
            # classifier (e.g. "JupSOL" → "jupiter" only) is the primary
            # failure mode this protects against.
            protocols = _augment_protocols_from_keywords(msg, protocols)

            is_category = bool(data.get("is_category_request", False))

            result = IntentResult(intent, confidence, protocols, "model", is_category)
            _cache_set(key, result)
            logger.info(
                "intent_router: msg=%r → intent=%s conf=%.2f protocols=%s",
                msg[:80], intent, confidence, list(protocols),
            )
            return result
        except Exception as exc:  # noqa: BLE001 — fail open to preserve current behavior
            logger.warning("intent_router classification failed: %s", exc)
            return IntentResult("ambiguous", 0.0, (), f"fallback:{type(exc).__name__}")


# ---------------------------------------------------------------------------
# Tool list filtering by intent
# ---------------------------------------------------------------------------

# Confidence threshold for taking action — below this, we don't trust the
# classifier enough to remove tools, so we leave the full set.
_HIGH_CONFIDENCE = 0.7


def filter_tools_by_intent(
    tools: list[dict],
    intent: IntentResult,
) -> list[dict]:
    """
    Soft filter: returns a (possibly identical) tool list narrowed to the
    intent. Falls back to the full list when confidence is low.

    Design note (lessons learned):
    - Earlier we ALSO dropped `query_onchain` for `intent=action` to make
      "balance fallback" structurally impossible. That broke real flows where
      the user expresses a single action message that legitimately needs
      data first ("add jupSOL/SOL to a meteora dlmm pool — list options
      first") — many of the read-only fetchers (`meteora_dlmm_get_pairs`,
      `kamino_market_reserves`, …) are now query_onchain types after the
      auto-migration in tool_selector. Filtering them out leaves the model
      with no way to research, so it falls silent → empty stream.
    - Action mis-routing (the original "sell all" → balance fallback
      bug) is now handled by the prompt rule + the explicit FORBIDDEN clause
      in `query_onchain`'s tool description. That's enough; we don't need
      the structural filter for action.
    - Advice still drops all tools — pure conversation should not pull
      protocol data the user didn't ask for.
    """
    # Category-request structural filter MUST run BEFORE BOTH the
    # confidence gate and the advice short-circuit. The category context
    # block is built deterministically server-side (keyword matcher OR
    # classifier flag), so the prose-only answer doesn't depend on the
    # classifier being confident. Gating this behind _HIGH_CONFIDENCE let
    # low-confidence category phrasings ("yield bearing stablecoins",
    # conf 0.66) keep the full tool set — the model then fired a
    # query_onchain, got partial data, and hedged instead of listing the
    # authoritative set. Drop tools whenever is_category_request is set.
    if intent.is_category_request:
        # Drop ALL tools. We tried "keep only execute_action + tool_choice
        # required" but mini emitted execute_action(swap) with empty params
        # AND no prose — the worst of both worlds. The right shape for
        # "what stables exist?" is just a prose list; the user can click
        # or ask for a swap as the next turn. Removing tools entirely
        # forces the model into prose-only mode, which is exactly what
        # the category context block prescribes.
        logger.info(
            "intent_router: category-request → dropped ALL %d tools (prose-only)",
            len(tools),
        )
        return []

    # Below the high-confidence bar (and not a category request) we don't
    # trust the classifier enough to narrow the tool set — keep everything.
    if intent.confidence < _HIGH_CONFIDENCE:
        return tools

    if intent.intent == "advice":
        # Advice = pure conversation / explanation. Earlier we experimented
        # with dropping tools here; that broke short picker follow-ups
        # ("let's stake that") where the model legitimately needs the
        # query/action tools. Leave the full set; the prompt rules handle
        # "don't fire a tool on chit-chat".
        logger.debug("intent=advice high-conf → keeping full tool set (no filter)")
        return tools

    # action / query / ambiguous: leave the full set.
    return tools


# Tool-name patterns that match pool listings / token directories — exactly
# the surface that biases the LLM toward "deposit" mini-apps when the user
# actually wanted to swap into a category of tokens.
_POOL_LIST_TOOL_PATTERNS: tuple[str, ...] = (
    "_search_pools", "_get_pools", "_get_pool_info", "_get_pool_keys",
    "_get_pools_by_lp", "_get_pools_v2", "_get_pool_position_history",
    "_get_pool_liquidity_history",
    "_get_token_list", "_get_token_prices", "_get_tokens",
    "raydium_get_farm_info", "raydium_get_farm_by_lp", "raydium_get_farm_keys",
    "meteora_dlmm_get_pairs", "meteora_dammv2_get_pools", "meteora_dammv1_get_pools",
    "orca_get_pools", "orca_search_pools", "orca_search_tokens",
    "jup_token_search",
)


def _tool_name(tool: dict) -> str:
    """Extract the function name from a tool dict, defensive across schemas."""
    fn = tool.get("function") if isinstance(tool, dict) else None
    if isinstance(fn, dict):
        n = str(fn.get("name") or "")
        if n:
            return n
    return str(tool.get("name") or "")


def _is_pool_or_token_list_tool(tool: dict) -> bool:
    """True when the tool is a pool-list / token-directory read.

    Tools are OpenAI function-call dicts: `{"type": "function", "function":
    {"name": ..., ...}}`. The name lives at `function.name` for both new
    (`type=function`) and legacy (`type=tool`) shapes — we read defensively
    so a schema change doesn't silently let pool tools leak back in.
    """
    name = _tool_name(tool)
    if not name:
        return False
    return any(p in name for p in _POOL_LIST_TOOL_PATTERNS)

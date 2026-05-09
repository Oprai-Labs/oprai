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

import hashlib
import json
import logging
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
    "kamino", "marginfi", "solend",
    "tensor", "magic_eden", "pumpfun",
    "relay", "debridge", "squid",
    "streamflow",
})


# Keyword augmentation: protocol-specific terms that, when present in the
# user message, force the protocol into the result regardless of what the
# LLM classifier picked. Belt-and-braces against a known failure mode where
# substring matches (e.g. "JupSOL" → "jupiter") cause the classifier to miss
# the actually-named protocol ("meteora", "DLMM"). All keywords are
# product-name tokens, language-agnostic. Word-boundary matched, lowercase.
_PROTOCOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "meteora":   ("meteora", "dlmm", "damm", "m3m3", "stake2earn"),
    "raydium":   ("raydium", "clmm"),
    "orca":      ("orca", "whirlpool"),
    "kamino":    ("kamino", "klend", "k-lend", "kswap"),
    "marginfi":  ("marginfi", "mrgn", "mfi"),
    "jito":      ("jito", "jitosol"),
    "marinade":  ("marinade", "msol"),
    "pumpfun":   ("pumpfun", "pump.fun", "pumpswap", "mayhem"),
    "magic_eden":("magiceden", "magic eden", "mmm pool"),
    "tensor":    ("tensor", "tensorians"),
    "streamflow":("streamflow",),
    "relay":     ("relay.link", "relay bridge"),
    "debridge":  ("debridge",),
    "squid":     ("squid router", "squidrouter"),
}


def _augment_protocols_from_keywords(
    msg: str, protocols: tuple[str, ...]
) -> tuple[str, ...]:
    """Add protocols whose strong keywords appear in the message.

    Returns a sorted tuple containing both the classifier's picks and any
    protocols whose product-name keyword appears verbatim. Idempotent — if
    the classifier already picked the protocol, this is a no-op.
    """
    if not msg:
        return protocols
    lowered = msg.lower()
    augmented = set(protocols)
    for proto, keywords in _PROTOCOL_KEYWORDS.items():
        if proto in augmented:
            continue
        if any(kw in lowered for kw in keywords):
            augmented.add(proto)
    return tuple(sorted(augmented))


@dataclass(frozen=True)
class IntentResult:
    intent: IntentClass
    confidence: float           # 0.0 – 1.0
    protocols: tuple[str, ...]  # canonical ids the user mentioned/implied; empty if none
    reason: str                 # short tag for logs ("model" / "fallback:err" / "cache")


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
  marginfi     — marginfi v2, mrgn, mfi.
  solend       — Solend (read-only; transactions deprecated).
  tensor       — Tensor NFT marketplace.
  magic_eden   — Magic Eden, ME, MMM pools.
  pumpfun      — pump.fun token launches, PumpSwap, mayhem.
  relay        — Relay.link cross-chain bridge.
  debridge     — deBridge cross-chain.
  squid        — Squid Router cross-chain (postHook destination calls).
  streamflow   — Streamflow token streaming / vesting.

Detection rules:
- The user often names a venue or product without naming the protocol — map
  it back: "DLMM" / "DAMM" / "m3m3" / "stake2earn" → meteora; "Whirlpool" →
  orca; "CLMM" → raydium; "K-Lend" / "kvault" / "kswap" → kamino; "MMM" →
  magic_eden; "jupSOL" → jupiter; "mSOL" → marinade; "jitoSOL" → jito.
- Multiple protocols in one message: include all of them. "swap on Raydium
  then deposit to Kamino" → ["raydium","kamino"].
- Ambiguous protocol words (e.g. "lend") with no other signal: leave the
  protocol list empty rather than guessing.
- A token symbol alone (BONK, USDC, WIF, RAY, SOL …) is NOT a protocol;
  emit empty protocols unless the message also names a venue.

Respond with ONLY a single-line JSON object, no other text:
{"intent":"<one of the four>", "confidence": <0.0-1.0>, "protocols": ["..."]}\
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

    async def classify(
        self,
        user_message: str,
        recent_context: str = "",
        timeout_s: float = 4.0,
    ) -> IntentResult:
        """
        Classify *user_message*. *recent_context* should be a short string
        with the last assistant turn(s) so referential phrases like "tamamını"
        ("all of it") can be resolved.

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

            result = IntentResult(intent, confidence, protocols, "model")
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
    - Action mis-routing (the original "tamamını sat" → balance fallback
      bug) is now handled by the prompt rule + the explicit FORBIDDEN clause
      in `query_onchain`'s tool description. That's enough; we don't need
      the structural filter for action.
    - Advice still drops all tools — pure conversation should not pull
      protocol data the user didn't ask for.
    """
    if intent.confidence < _HIGH_CONFIDENCE:
        return tools

    if intent.intent == "advice":
        # Earlier this branch dropped tools entirely — first all of them,
        # then just `query_onchain`. Both broke real flows: short picker
        # follow-ups ("ona stake edelim ok?", "let's go with the biggest")
        # often end with a particle that makes the classifier read them
        # as advice. With tools stripped, the model can only narrate
        # ("İşlem başladı, kart açılacak…" with NO card) or beg the user
        # for data it could trivially fetch ("how much jitoSOL do you
        # have?" when balance is one query_onchain call away).
        # The downside the filter was supposed to prevent — model picking
        # a tool on conversational turns — is already covered by the
        # prompt rules. Let the model decide; trust the system prompt.
        logger.debug("intent=advice high-conf → keeping full tool set (no filter)")
        return tools

    # action / query / ambiguous: leave the full set.
    return tools

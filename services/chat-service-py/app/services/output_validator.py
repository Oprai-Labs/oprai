"""Second-pass LLM sanity check on tool calls before they reach the user.

The responder model emits a tool call (`execute_action` / `query_onchain` /
`request_clarification`). Each call has already been parsed against a
Pydantic schema and address-format-validated. This module adds a third
gate: a small fast LLM examines the call against the user's actual message
and returns a verdict — does this action *make sense* for what the user
asked?

Why: schema validation catches malformed JSON, address regex catches
gibberish, but neither catches "user asked to swap 0.1 SOL → assistant
emits transfer_token to=Hw...XtK6 amount=5". That kind of semantic drift
is what the validator catches.

Cost is negligible (~$0.0001 per call with gpt-4o-mini, 200 input tokens
and 50 output tokens), and the latency adds <300ms which lands during the
streaming text generation anyway.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.config import settings
from app.services.tokens_generated import VERIFIED_TOKENS

logger = logging.getLogger(__name__)


_CLIENT: Any = None
_HTTP_CLIENT: Any = None  # httpx.AsyncClient — lazy

# Address fields that carry token mints across all action types. When we see
# a base58 string in one of these, we attempt symbol resolution before the
# value is sent to the validator LLM. Add a key here when a new action
# introduces another mint-bearing parameter.
_MINT_FIELD_KEYS: frozenset[str] = frozenset({
    "inputMint", "outputMint", "mint", "token", "bank",
    "tokenA", "tokenB", "tokenXMint", "tokenYMint",
    "fromMint", "toMint",
})

# Solana base58 address shape (32–44 alphanumeric, no I/O/l/0).
_BASE58_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Pre-built lookup table from the curated VERIFIED_TOKENS list. Hits here
# are instant and require no network call. Long-tail tokens fall through
# to the Jupiter API resolver.
_LOCAL_ADDR_TO_SYMBOL: dict[str, str] = {
    t["address"]: t["symbol"] for t in VERIFIED_TOKENS
}

# In-process TTL cache for Jupiter resolutions so repeat addresses inside
# one conversation cost zero. Negative cache (`None`) prevents thrashing
# when a mint genuinely isn't in Jupiter's index.
_JUP_CACHE: dict[str, tuple[str | None, float]] = {}
_JUP_CACHE_TTL_S = 60 * 60  # 1 hour


async def _resolve_address_to_symbol(address: str) -> str | None:
    """Return the token symbol for an address, or None when unknown.

    Lookup order:
      1. Local VERIFIED_TOKENS map  (instant, ~50 entries — covers SOL/USDC/
         USDT/USDS/jupSOL/jitoSOL/mSOL/bSOL/BONK/JUP/JTO/WIF/etc.)
      2. Jupiter V2 search API      (covers the long tail; cached 1h)

    Failures (network error, 4xx) cache a None for the TTL window so we
    don't hammer Jupiter when a mint legitimately doesn't exist.
    """
    if not address:
        return None
    sym = _LOCAL_ADDR_TO_SYMBOL.get(address)
    if sym:
        return sym

    cached = _JUP_CACHE.get(address)
    if cached is not None and (time.time() - cached[1]) < _JUP_CACHE_TTL_S:
        return cached[0]

    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        try:
            import httpx  # type: ignore[import-not-found]
            _HTTP_CLIENT = httpx.AsyncClient(timeout=2.0)
        except Exception:
            return None

    try:
        resp = await _HTTP_CLIENT.get(
            "https://lite-api.jup.ag/tokens/v2/search",
            params={"query": address, "limit": 1},
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                sym = data[0].get("symbol")
            elif isinstance(data, dict):
                items = data.get("tokens") or data.get("data") or []
                sym = items[0].get("symbol") if items else None
            else:
                sym = None
        else:
            sym = None
    except Exception:
        sym = None

    _JUP_CACHE[address] = (sym, time.time())
    return sym


async def _normalize_args_for_validator(args: dict) -> dict:
    """Replace base58 mint addresses in args with their symbols so the
    validator LLM sees `outputMint: "USDC"` instead of `outputMint:
    "EPjFWdd5..."`. Falls back to the original address when the resolver
    can't identify it (long-tail asset). The original args dict is NOT
    mutated — a shallow copy is returned.
    """
    if not isinstance(args, dict):
        return args
    out = dict(args)
    params = out.get("params")
    if not isinstance(params, dict):
        return out
    new_params = dict(params)
    for k, v in list(new_params.items()):
        if k not in _MINT_FIELD_KEYS or not isinstance(v, str):
            continue
        if not _BASE58_ADDR_RE.match(v):
            continue  # already a symbol or short string
        sym = await _resolve_address_to_symbol(v)
        if sym:
            new_params[k] = sym  # canonical symbol form
    out["params"] = new_params
    return out


_VALIDATOR_SYSTEM = """You audit tool calls produced by a Solana DeFi assistant.
You receive (1) the user's most recent message, (2) the tool call the
assistant intends to make. Decide whether the call is a reasonable response
to that message, then return a JSON verdict.

IMPORTANT — multi-language awareness:
The user message may be in any language. Judge intent by grammatical mood and
context, not by language. A short message that pairs an asset name and/or
amount with an action verb in imperative form is a command to execute — not a
question about rates or mechanics. Do NOT classify imperative commands as
question mismatches just because they are not in English.

Look for:
- Action vs question mismatch — only flag when the user is ASKING (e.g., "what
  is the SOL price?", "show me the APY") but the assistant calls execute_action.
  Do NOT flag imperative commands in any language as mismatches.
- Token / amount mismatch ("swap 0.1 SOL" but params show amount=5 or
  inputMint=USDC).

  **CRITICAL — `swapMode` decides which side `amount` belongs to:**
  - `swapMode: "ExactIn"` (or omitted, the default): `amount` is the
    **input** quantity (in `inputMint` units). User says "swap 5 SOL to
    USDC" → `inputMint=SOL, amount=5, swapMode=ExactIn` (or absent).
  - `swapMode: "ExactOut"`: `amount` is the **output** quantity (in
    `outputMint` units). User says "buy 5 USDC for SOL" → `inputMint=SOL,
    outputMint=USDC, amount=5, swapMode=ExactOut`. The 5 IS 5 USDC, not
    5 SOL. The amount of SOL the user spends is computed at quote time
    and is unknown here. **Do NOT block this as a "swapping 5 SOL"
    mismatch — under ExactOut semantics, amount=5 means 5 of the
    OUTPUT token, which IS what the user asked for.**
  Mismatch only exists when:
    - `inputMint` ≠ token user said to spend / sell, OR
    - `outputMint` ≠ token user said to buy / receive, OR
    - `amount` ≠ the number the user typed (regardless of side).
- Wrong protocol routing (user said "kamino" but action_type=jupiter_*).
- Address-shaped fabrication (params contain a base58 mint but the user
  never mentioned that token by symbol or address; cross-reference the
  user message text).
- **Token-symbol substitution / hallucination**: if the user named a token
  by symbol (USDS, JUP, BONK, JTO, INF, JLP, jupSOL, jitoSOL, msol, bsol,
  USDC, USDT, etc.), the action params MUST contain that EXACT symbol or
  its canonical mint. Substituting a similar-looking token (USDS → USDC,
  jupSOL → SOL, mSOL → SOL, BONK → BONKE, JLP → JUP, USDT → USDC) is a
  BLOCK-level fabrication — the assistant invented the token. The fix is
  to call jup_token_search first to resolve the symbol the user actually
  said, not to pick a familiar substitute.

  Note: by the time you see the action, ALL canonical mint addresses have
  been replaced by their token symbols (SOL, USDC, jupSOL, …) via the
  upstream resolver — so a verbatim symbol comparison is reliable. If a
  field still contains a long base58 address, that means the resolver
  could NOT identify the token (it's neither in our verified registry nor
  in Jupiter's index). Treat that as a yellow flag: probably a long-tail
  asset, not necessarily fabrication.
- **Input-token fabrication**: for swap / send / trade actions, if the user
  did NOT specify the source asset and there is no clear prior context for
  it, the assistant MUST NOT guess (e.g. assuming inputMint=SOL just because
  the wallet has SOL). It should ask via request_clarification. Filling the
  input token without justification is a BLOCK.
  EXCEPTION: when the user named a TOKEN PAIR (e.g. "USDS-USDC pool",
  "SOL/JUP havuz"), BOTH tokens are valid inputs. The assistant picking
  EITHER side of the named pair is NOT fabrication — it is a valid
  single-sided deposit. Do NOT block "user said USDS-USDC, action uses
  USDC" or "user said SOL-JUP, action uses SOL". Both sides are legitimate
  for liquidity actions on the named pair.
- Unsafe parameter values (transferring to "11111111111111111111111111111111"
  is the system program, never a user wallet).

Do NOT flag:
- Minor stylistic choices (which staking provider when user said "stake").
- Reasonable defaults (default slippage, default fee tier).
- Cases where the assistant is asking for clarification — those are fine.
- Imperative / command phrasings in any language as "question mismatch".
- **Single-sided liquidity / CLMM open_position actions where ONE token from
  a multi-token pair is missing from params.** Actions like
  `raydium_open_position`, `orca_open_position`, `meteora_open_position`,
  `meteora_add_to_position`, `meteora_dlmm_add_liquidity` are SINGLE-SIDED
  by design: the user deposits one side of a pair, and the other side is
  implied by the `poolId` (the pool already knows both mints). If the
  action has `poolId` + `inputMint` (or equivalent) and the user named ONE
  of the pool's two tokens, that is COMPLETE — do NOT flag "the other
  token is missing". The pool fills in the other side automatically. The
  ratio engine in the action card frontend lives precisely to make this
  flow work.
- **Liquidity actions where the user named a pair and gave just one
  amount.** "Add 4 USDC to USDS/USDC pool" → action with
  `tokenA=USDC, tokenB=USDS, amountA=4` (or `inputMint=USDC, inputAmount=4`)
  is correct. The card's ratio engine fills the other side at sign time.
  Do NOT block this — it is the canonical single-side deposit.

Output JSON exactly:
{
  "ok": true | false,
  "reason": "<short — only when ok=false>",
  "severity": "block" | "warn"   // only when ok=false; "block" stops the call
}

Severity guidance:
- "block": fabricated address, wrong token, dangerous wallet (system
  program, all-zeros, etc.), clear action-vs-question mismatch.
- "warn":  ambiguous routing, suboptimal but legal parameters.

When ok=true return {"ok": true} only — no extra fields."""


# ── Deterministic buy/sell direction guard ───────────────────────────────
# Money-critical: a buy-typed action emitted for a clear "sell" request (or
# vice versa) must never reach the user's wallet, regardless of how good the
# responder model is. This runs BEFORE the LLM validator and is fully
# deterministic. Scoped to the pump.fun / PumpSwap trade actions, whose
# direction is unambiguous (unlike swaps, where direction is token-based and
# left to the LLM validator).
_BUY_DIRECTION_ACTIONS: frozenset[str] = frozenset({"pumpfun_buy", "pumpswap_buy"})
_SELL_DIRECTION_ACTIONS: frozenset[str] = frozenset({"pumpfun_sell", "pumpswap_sell"})

# Turkish "satın al…" means BUY but contains the substring "sat" (sell). We
# strip every "satın…" form before sell-matching so it can't false-trigger.
_SATIN_STRIP_RE = re.compile(r"sat[ıi]n\w*", re.IGNORECASE | re.UNICODE)
_BUY_INTENT_RE = re.compile(
    r"(?:\bbuy\b|\blong\b|\bape\b|\bcomprar\b|\bkauf\w*|"
    r"sat[ıi]n\s*al\w*|\bal[ıi]m\w*|\balmak\b|\balay[ıi]m\b|\balabilir\w*|\bal\b)",
    re.IGNORECASE | re.UNICODE,
)
_SELL_INTENT_RE = re.compile(
    r"(?:\bsell\b|\bdump\b|\bvender\b|\bverkauf\w*|elden\s*ç[ıi]kar\w*|"
    r"\bboşalt\w*|\bsat\w*)",
    re.IGNORECASE | re.UNICODE,
)


def _deterministic_direction_conflict(user_message: str, args: dict) -> str | None:
    """Return a block reason when the action's trade direction contradicts an
    unambiguous buy/sell intent in the user's message, else None.

    Conservative by design: blocks ONLY on a clear one-sided conflict (user
    says sell-only but action buys, or user says buy-only but action sells).
    If the message carries both signals (e.g. "sell X, buy Y") or neither,
    it defers to the LLM validator — never a false block.
    """
    if not isinstance(args, dict) or not user_message:
        return None
    action_type = str(args.get("action_type", "") or "")
    is_buy = action_type in _BUY_DIRECTION_ACTIONS
    is_sell = action_type in _SELL_DIRECTION_ACTIONS
    if not (is_buy or is_sell):
        return None

    text = user_message.lower()
    buy_hit = bool(_BUY_INTENT_RE.search(text))
    # Remove "satın…" spans so the BUY word "satın (al)" can't match SELL.
    sell_hit = bool(_SELL_INTENT_RE.search(_SATIN_STRIP_RE.sub(" ", text)))

    if is_buy and sell_hit and not buy_hit:
        return f"user asked to SELL but action is a BUY ({action_type})"
    if is_sell and buy_hit and not sell_hit:
        return f"user asked to BUY but action is a SELL ({action_type})"
    return None


class ValidatorVerdict:
    __slots__ = ("ok", "reason", "severity")

    def __init__(self, ok: bool, reason: str = "", severity: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.severity = severity

    @property
    def should_block(self) -> bool:
        return (not self.ok) and self.severity == "block"


async def validate_tool_call(
    user_message: str,
    tool_name: str,
    tool_args: dict | str,
) -> ValidatorVerdict:
    """Run the second-pass check.

    Failure modes (network error, invalid JSON, missing API key) return an
    "ok=True" verdict — fail-open. The validator is a backstop, not a hard
    gate; the schema + address checks already ran upstream.
    """
    # Deterministic buy/sell direction guard — runs first, no LLM, no key
    # needed, provider-agnostic. Catches the money-critical case where a
    # buy-typed trade is emitted for a clear sell request (or vice versa).
    try:
        _args_for_dir = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
        if isinstance(_args_for_dir, dict):
            _dir_reason = _deterministic_direction_conflict(user_message, _args_for_dir)
            if _dir_reason:
                logger.warning("direction_guard blocked tool call: %s", _dir_reason)
                return ValidatorVerdict(False, reason=_dir_reason, severity="block")
    except Exception:
        logger.debug("direction guard skipped on error", exc_info=True)

    api_key = (settings.OPRAI_OPENAI_API_KEY or "").strip()
    if not api_key:
        return ValidatorVerdict(True)

    global _CLIENT
    if _CLIENT is None:
        try:
            from openai import AsyncOpenAI

            _CLIENT = AsyncOpenAI(api_key=api_key)
        except Exception:
            logger.debug("output_validator: OpenAI init failed", exc_info=True)
            return ValidatorVerdict(True)

    if isinstance(tool_args, str):
        try:
            args = json.loads(tool_args)
        except json.JSONDecodeError:
            return ValidatorVerdict(
                False,
                reason="tool args were not valid JSON",
                severity="block",
            )
    else:
        args = tool_args

    # Normalize mint addresses → symbols BEFORE the validator sees them.
    # Without this the LLM has to know "EPjFWdd5… == USDC" and routinely
    # gets it wrong, blocking legitimate actions. After this step the
    # validator sees `outputMint: "USDC"` so a verbatim symbol comparison
    # against the user's message works.
    try:
        normalized_args = await _normalize_args_for_validator(args)
    except Exception:
        logger.debug("address normalization failed, using raw args", exc_info=True)
        normalized_args = args

    payload = {
        "user_message": user_message,
        "tool_name": tool_name,
        "tool_args": normalized_args,
    }

    try:
        resp = await _CLIENT.chat.completions.create(
            model=settings.OPRAI_SUMMARIZER_MODEL,  # reuse the cheap model
            messages=[
                {"role": "system", "content": _VALIDATOR_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_tokens=120,
            temperature=0.0,
        )
    except Exception:
        logger.debug("output_validator call failed — failing open", exc_info=True)
        return ValidatorVerdict(True)

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return ValidatorVerdict(True)
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("output_validator returned non-JSON: %r", raw[:200])
        return ValidatorVerdict(True)
    if not isinstance(verdict, dict):
        return ValidatorVerdict(True)

    ok = bool(verdict.get("ok", True))
    reason = str(verdict.get("reason", "") or "")
    severity = str(verdict.get("severity", "") or "")
    if not ok and severity not in ("block", "warn"):
        # Defensive default — if the validator says "not ok" but doesn't pick
        # a severity, treat as a warning rather than blocking the user.
        severity = "warn"
    return ValidatorVerdict(ok=ok, reason=reason, severity=severity)

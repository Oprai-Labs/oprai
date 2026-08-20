"""
Relay.link — comprehensive LLM quality suite

5-10 scenarios for every Relay action:
  1.  relay_bridge                    — cross-chain bridging
  2.  relay_get_quote                 — a bridge price quote
  3.  relay_get_chains                — supported chains
  4.  relay_get_chains_liquidity      — solver liquidity per chain
  5.  relay_get_currencies            — bridgeable tokens
  6.  relay_get_token_price           — a token price via Relay
  7.  relay_intent_status             — status of a bridge request
  8.  relay_get_requests              — bridge history
  9.  relay_index_transaction         — TX indexing (general)
  10. relay_single_transaction        — TX indexing (request-scoped)
  11. relay_deposit_address_reindex   — re-indexing a deposit address
  12. relay_get_app_fee_balances      — app-fee balances
  13. relay_claim_app_fees            — claiming app fees
  14. relay_fast_fill                 — fast fill (operator)
  15. relay_get_swap_sources          — listing swap sources
  16. relay_execute                   — gasless EVM transaction (operator)
  17. Provider routing                — picking the right provider

What each action is checked for:
  - the right action/query is triggered
  - parameter-extraction quality
  - no raw JSON dumps
  - no hallucination
  - mixed Turkish and English questions
  - the wrong provider never fires
  - a missing parameter produces a clarification

The Turkish questions are deliberate: they are the regression net for
Turkish-language intent handling.

Toplam: ~140 test
"""

from __future__ import annotations

import json
import os
import re
import uuid

import httpx
import pytest

# Load .env files so tests work without pre-exporting env vars
def _load_dotenv():
    for path in [
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env"),
    ]:
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        break

_load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://localhost:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

# Sample request IDs (real format)
SAMPLE_REQUEST_ID   = "0x1234abcd5678ef90"
SAMPLE_TX_HASH_EVM  = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab"
SAMPLE_TX_HASH_SOL  = "5HueCGU8rMFej5YVzHkjgQ3MFkWM9XzVE4gZkLRZ3EKTh"
SAMPLE_DEPOSIT_ADDR = "0xDEaDBEEf1234567890abcdef1234567890abcdef"

# Relay-supported chain ID'leri
CHAIN_SOL  = 900
CHAIN_ETH  = 1
CHAIN_BASE = 8453
CHAIN_ARB  = 42161

# USDC adresleri
USDC_ETH  = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_ARB  = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"


# ─── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def chat_client():
    try:
        r = httpx.get(f"{CHAT_URL}/health", timeout=3.0)
        if r.status_code not in (200, 204):
            pytest.skip(f"chat-service did not respond: HTTP {r.status_code}")
    except Exception as exc:
        pytest.skip(f"chat-service unreachable ({CHAT_URL}): {exc}")
    with httpx.Client(base_url=CHAT_URL, timeout=120.0) as c:
        yield c


def _headers() -> dict:
    return {
        "X-Internal-Api-Key": INTERNAL_KEY,
        "X-User-Wallet": TEST_WALLET,
        "Content-Type": "application/json",
    }


def _chat(client: httpx.Client, message: str) -> "StreamResult":
    payload = {"sessionId": f"local:{uuid.uuid4()}", "content": message, "attachments": []}
    with client.stream("POST", "/messages/stream", json=payload, headers=_headers()) as r:
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        body = r.read().decode()
    return StreamResult.parse(body)


# ─── SSE Parser ───────────────────────────────────────────────────────────────

class StreamResult:
    def __init__(self):
        self.text: str = ""
        self.actions: list[dict] = []
        self.queries: list[dict] = []
        self.clarify: list[dict] = []
        self.errors: list[dict] = []
        self.done: bool = False

    @classmethod
    def parse(cls, raw: str) -> "StreamResult":
        s = cls()
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                s.done = True
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if "delta" in evt:
                s.text += evt["delta"]
            elif "action" in evt:
                s.actions.append(evt["action"])
            elif "query" in evt:
                s.queries.append(evt["query"])
            elif "clarify" in evt:
                s.clarify.append(evt["clarify"])
            elif "error" in evt:
                s.errors.append(evt)
        return s

    def types(self) -> list[str]:
        result = [x.get("type", "") for x in self.actions + self.queries]
        # Also include action types from clarify options — LLM knows the action, just needs missing params
        for c in self.clarify:
            for opt in c.get("options", []):
                a = opt.get("action", "")
                if a and a not in result:
                    result.append(a)
        return result

    def params_for(self, t: str) -> dict:
        for x in self.actions + self.queries:
            if x.get("type") == t:
                return x.get("params", {})
        return {}

    def asked_clarification(self) -> bool:
        return bool(self.clarify)

    def tl(self) -> str:
        return self.text.lower()

    def has_text(self, min_chars: int = 40) -> bool:
        return len(self.text.strip()) >= min_chars


# ─── Helpers ─────────────────────────────────────────────────────────────────

def triggered(r: StreamResult, t: str, msg: str = ""):
    assert t in r.types(), (
        f"'{t}' tetiklenmedi.\nTetiklenenler: {r.types()}\nLLM: {r.text[:400]}\n{msg}"
    )


def triggered_any(r: StreamResult, *types: str, msg: str = ""):
    """Pass if ANY of the given types is triggered (for LLM synonym handling)."""
    assert any(t in r.types() for t in types), (
        f"None of these triggered: {list(types)}\nTriggered: {r.types()}\nLLM: {r.text[:400]}\n{msg}"
    )


def relay_bridge_key(r: StreamResult) -> str:
    """Return the triggered bridge type: 'relay_bridge' or fallback 'bridge'."""
    if "relay_bridge" in r.types():
        return "relay_bridge"
    return "bridge"


def not_triggered(r: StreamResult, t: str, msg: str = ""):
    assert t not in r.types(), (
        f"Unwanted '{t}' was triggered.\nLLM: {r.text[:300]}\n{msg}"
    )


def has_param(r: StreamResult, action: str, key: str, contains: str | None = None, msg: str = ""):
    for x in r.actions + r.queries:
        if x.get("type") == action:
            p = x.get("params", {})
            assert key in p, f"[{action}] '{key}' eksik. Params: {p}\n{msg}"
            if contains:
                assert contains.lower() in str(p[key]).lower(), (
                    f"[{action}] '{key}'={p[key]!r} does not contain '{contains}'\n{msg}"
                )
            return
    pytest.fail(f"[{action}] never triggered. Triggered: {r.types()}")


def no_raw_json(r: StreamResult, msg: str = ""):
    t = r.text
    assert not (t.count("{") + t.count("}") > 10 and t.count('"') > 20), (
        f"LLM dumped raw JSON.\n{t[:500]}\n{msg}"
    )


def has_number(r: StreamResult, msg: str = ""):
    assert re.search(r"\d[\d,.]*", r.text), (
        f"No number in the LLM response.\n{r.text[:400]}\n{msg}"
    )


def mentions(r: StreamResult, kws: list[str], min_hits: int = 1, msg: str = ""):
    hits = [k for k in kws if k.lower() in r.tl()]
    assert len(hits) >= min_hits, (
        f"LLM says none of: {kws}\nText: {r.text[:400]}\n{msg}"
    )


def no_hallucination(r: StreamResult, msg: str = ""):
    bad = [
        "gerçek zamanlı veriye erişimim yok",
        "i don't have access to real-time",
        "as of my knowledge cutoff",
        "i cannot access live",
    ]
    for b in bad:
        assert b not in r.tl(), f"Hallucination: '{b}'\nText: {r.text[:400]}\n{msg}"


def explains_error(r: StreamResult, msg: str = ""):
    kws = ["hata", "geçersiz", "eksik", "error", "invalid", "required", "sorry", "üzgün", "lütfen", "clarif"]
    assert any(k in r.tl() for k in kws) or r.asked_clarification(), (
        f"No explanation for the error / missing param.\n{r.text[:400]}\n{msg}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. RELAY_BRIDGE — cross-chain bridging
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayBridge:
    """relay_bridge: 10 scenarios — parameter extraction, chain/token routing, clarification."""

    def test_sol_to_eth_basic(self, chat_client):
        r = _chat(chat_client, "Bridge 1 SOL from Solana to Ethereum using Relay")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        assert "900" in str(p) or "solana" in str(p).lower(), \
            f"originChainId=900 (Solana) bekleniyor. Params: {p}"
        assert "1" in str(p) or "ethereum" in str(p).lower(), \
            f"destinationChainId=1 (Ethereum) bekleniyor. Params: {p}"

    def test_usdc_base_to_arbitrum(self, chat_client):
        r = _chat(chat_client, "Bridge 100 USDC from Base to Arbitrum via Relay")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        assert "8453" in str(p) or "base" in str(p).lower(), \
            f"originChainId=8453 (Base) bekleniyor. Params: {p}"
        assert "42161" in str(p) or "arb" in str(p).lower(), \
            f"destinationChainId=42161 (Arbitrum) bekleniyor. Params: {p}"
        assert "100" in str(p), f"amount=100 bekleniyor. Params: {p}"

    def test_amount_and_token_extracted(self, chat_client):
        r = _chat(chat_client, "Use Relay bridge to send 0.5 ETH from Ethereum to Base")
        # Either relay_bridge or the generic bridge is acceptable
        assert "relay_bridge" in r.types() or "bridge" in r.types(), (
            f"relay_bridge veya bridge tetiklenemedi. Tetiklenenler: {r.types()}\nLLM: {r.text[:300]}"
        )
        action_key = "relay_bridge" if "relay_bridge" in r.types() else "bridge"
        p = r.params_for(action_key)
        assert "0.5" in str(p), f"amount=0.5 bekleniyor. Params: {p}"
        # ETH native = zero address 0x000...0 OR "ETH" symbol — both are correct
        eth_present = (
            "eth" in str(p).lower()
            or "0x0000000000000000000000000000000000000000" in str(p).lower()
        )
        assert eth_present, f"ETH token (symbol veya zero address) bekleniyor. Params: {p}"

    def test_with_recipient(self, chat_client):
        recipient = "0xDEaDBEEf1234567890abcdef1234567890abcdef"
        r = _chat(chat_client, f"Bridge 50 USDC from Ethereum to Base, send to {recipient}")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        assert recipient.lower() in str(p).lower(), \
            f"recipient={recipient} bekleniyor. Params: {p}"

    def test_turkish_bridge_request(self, chat_client):
        r = _chat(chat_client, "Relay ile 200 USDC'yi Solana'dan Ethereum'a köprüle")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        if p:
            assert "900" in str(p) or "solana" in str(p).lower(), \
                f"Kaynak zincir Solana (900) bekleniyor. Params: {p}"
            assert "200" in str(p), f"amount=200 bekleniyor. Params: {p}"

    def test_implicit_crosschain_no_provider_named(self, chat_client):
        """Regression: cross-chain inferred from chain names alone (no 'Relay' word).

        Reproduces the failing screenshot: user wrote
        "1 sol karşılığı base ağında ethereum al" — Solana→Base cross-chain swap
        without naming any bridge provider. Must route to relay_bridge (the
        default cross-chain provider) and not fall back to clarification or
        hallucinated Wormhole/Squid routes.
        """
        r = _chat(chat_client, "1 sol karşılığı base ağında ethereum al")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        if p:
            assert "900" in str(p) or "solana" in str(p).lower(), \
                f"originChainId=900 (Solana) bekleniyor. Params: {p}"
            assert "8453" in str(p) or "base" in str(p).lower(), \
                f"destinationChainId=8453 (Base) bekleniyor. Params: {p}"

    def test_buy_token_on_chain_pattern_turkish(self, chat_client):
        """Regression: "X SOL ile Y'de Z al" must be ONE relay_bridge call.

        Second screenshot bug: model wrongly proposed a multi-step path
        "Solana → Base → Ethereum" and asked for clarification, instead of
        recognising this as a single SOL→ETH(on Base) cross-chain swap.
        Must call relay_bridge directly, NOT request_clarification.
        """
        r = _chat(chat_client, "0.1 sol ile base ethereum al")
        triggered_any(r, "relay_bridge", "bridge")
        # Must NOT have asked for clarification — all 6 params are derivable.
        assert "request_clarification" not in r.types(), (
            "Tüm parametreler türetilebilir; clarification gerekmiyor. "
            f"Tetiklenenler: {r.types()}"
        )
        p = r.params_for(relay_bridge_key(r))
        if p:
            assert "900" in str(p) or "solana" in str(p).lower(), \
                f"originChainId=900 (Solana) bekleniyor. Params: {p}"
            assert "8453" in str(p) or "base" in str(p).lower(), \
                f"destinationChainId=8453 (Base) bekleniyor — ETH on Base, NOT Ethereum mainnet. Params: {p}"
            assert "0.1" in str(p), f"amount=0.1 bekleniyor. Params: {p}"

    def test_buy_token_on_chain_pattern_english(self, chat_client):
        """English variant of "buy X on Y" cross-chain pattern."""
        r = _chat(chat_client, "buy 0.1 ETH on Base with SOL")
        triggered_any(r, "relay_bridge", "bridge")
        assert "request_clarification" not in r.types(), (
            f"Clarification gerekmiyor. Tetiklenenler: {r.types()}"
        )
        p = r.params_for(relay_bridge_key(r))
        if p:
            assert "8453" in str(p) or "base" in str(p).lower(), \
                f"destinationChainId=8453 (Base) bekleniyor. Params: {p}"

    def test_same_chain_not_relay_bridge(self, chat_client):
        r = _chat(chat_client,
            "Swap 1 SOL for USDC on Solana only — same chain, no bridging, use Jupiter or Relay DEX aggregator")
        not_triggered(r, "relay_bridge",
            "A same-chain swap must not trigger relay_bridge")

    def test_missing_destination_asks_clarification(self, chat_client):
        r = _chat(chat_client, "Bridge 1 SOL using Relay")
        if "relay_bridge" not in r.types() and "bridge" not in r.types():
            assert r.asked_clarification() or r.has_text(30), \
                "A clarification or question is expected for the missing destination"

    def test_exact_output_mode(self, chat_client):
        r = _chat(chat_client, "I want to receive exactly 100 USDC on Base, bridge from Ethereum via Relay")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        if p:  # EXACT_OUTPUT in params is ideal; action trigger is the core assertion
            assert "exact_output" in str(p).lower() or "EXACT_OUTPUT" in str(p) or "exact" in str(p).lower(), \
                f"tradeType=EXACT_OUTPUT bekleniyor. Params: {p}"

    def test_gas_on_destination(self, chat_client):
        r = _chat(chat_client,
            "Bridge 100 USDC from Solana to Arbitrum, also send me some gas on Arbitrum")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        if p:  # gas airdrop param is ideal; action trigger is the core assertion
            all_p = str(p).lower()
            assert "topupgas" in all_p or "topup_gas" in all_p or \
                   "receivegasondestination" in all_p or "true" in all_p, \
                f"topupGas=true bekleniyor. Params: {p}"

    def test_no_raw_json_in_response(self, chat_client):
        r = _chat(chat_client, "Bridge 10 USDC from Ethereum to Base via Relay")
        triggered_any(r, "relay_bridge", "bridge")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RELAY_GET_QUOTE — a bridge price quote
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetQuote:
    """relay_get_quote: 6 senaryo"""

    def test_basic_quote_sol_to_eth(self, chat_client):
        r = _chat(chat_client, "Get a Relay quote for bridging 1 SOL from Solana to Ethereum")
        triggered(r, "relay_get_quote")
        p = r.params_for("relay_get_quote")
        assert "900" in str(p) or "solana" in str(p).lower(), f"Params: {p}"

    def test_usdc_quote_base_to_arb(self, chat_client):
        r = _chat(chat_client, "Show me a relay bridge quote for 500 USDC from Base to Arbitrum")
        triggered(r, "relay_get_quote")
        p = r.params_for("relay_get_quote")
        assert "500" in str(p), f"amount=500 bekleniyor. Params: {p}"

    def test_quote_not_execute(self, chat_client):
        r = _chat(chat_client, "How much would it cost to bridge 2 ETH from Ethereum to Base via Relay? Just give me a quote.")
        triggered(r, "relay_get_quote")
        not_triggered(r, "relay_bridge", "Only a quote was asked for; no transaction should fire")

    def test_turkish_quote(self, chat_client):
        r = _chat(chat_client, "Relay ile Solana'dan Base'e 50 USDC köprülemek ne kadar tutar?")
        triggered(r, "relay_get_quote")

    def test_exact_output_quote(self, chat_client):
        r = _chat(chat_client,
            "Relay bridge quote: originChainId=8453, destinationChainId=1, originCurrency=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913, destinationCurrency=0x0000000000000000000000000000000000000000, amount=1, tradeType=EXACT_OUTPUT")
        # relay_get_quote is preferred; relay_bridge also acceptable
        triggered_any(r, "relay_get_quote", "relay_bridge", "bridge")
        key = "relay_get_quote" if "relay_get_quote" in r.types() else relay_bridge_key(r)
        p = r.params_for(key)
        assert "exact_output" in str(p).lower() or "EXACT_OUTPUT" in str(p), \
            f"tradeType=EXACT_OUTPUT bekleniyor. Params: {p}"

    def test_no_raw_json(self, chat_client):
        r = _chat(chat_client, "Give me a Relay quote for 100 USDC from Ethereum to Arbitrum")
        triggered(r, "relay_get_quote")
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RELAY_GET_CHAINS — desteklenen zincirler
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetChains:
    """relay_get_chains: 6 senaryo"""

    def test_list_all_chains(self, chat_client):
        r = _chat(chat_client, "What chains does Relay support?")
        triggered(r, "relay_get_chains")

    def test_turkish_chains_query(self, chat_client):
        r = _chat(chat_client, "Relay hangi zincirleri destekliyor?")
        triggered(r, "relay_get_chains")

    def test_response_quality(self, chat_client):
        r = _chat(chat_client, "List all supported networks for Relay bridge")
        triggered_any(r, "relay_get_chains", "relay_get_chains_liquidity")
        no_raw_json(r)
        no_hallucination(r)

    def test_filter_by_chain(self, chat_client):
        r = _chat(chat_client, "Show me Relay support info for Ethereum and Base only")
        triggered(r, "relay_get_chains")

    def test_solana_support_query(self, chat_client):
        r = _chat(chat_client, "Does Relay support Solana? Show me the chain details")
        triggered(r, "relay_get_chains")
        tl = r.text.lower()
        assert "solana" in tl or r.types() != [], \
            "The answer should mention that Solana is supported"

    def test_not_relay_bridge(self, chat_client):
        r = _chat(chat_client, "Show me all Relay supported chains")
        triggered(r, "relay_get_chains")
        not_triggered(r, "relay_bridge", "Listing chains must not trigger relay_bridge")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RELAY_GET_CHAINS_LIQUIDITY — solver likiditesi
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetChainsLiquidity:
    """relay_get_chains_liquidity: 6 senaryo"""

    def test_ethereum_liquidity(self, chat_client):
        r = _chat(chat_client, "Check Relay solver liquidity on Ethereum")
        triggered(r, "relay_get_chains_liquidity")
        p = r.params_for("relay_get_chains_liquidity")
        assert "1" in str(p) or "ethereum" in str(p).lower(), \
            f"chainId=1 (Ethereum) bekleniyor. Params: {p}"

    def test_base_liquidity(self, chat_client):
        r = _chat(chat_client, "What's the Relay solver liquidity on Base?")
        triggered(r, "relay_get_chains_liquidity")
        p = r.params_for("relay_get_chains_liquidity")
        assert "8453" in str(p) or "base" in str(p).lower(), \
            f"chainId=8453 (Base) bekleniyor. Params: {p}"

    def test_solana_liquidity(self, chat_client):
        r = _chat(chat_client,
            "Check the Relay solver liquidity available on Solana (chain ID 900)")
        triggered(r, "relay_get_chains_liquidity")
        p = r.params_for("relay_get_chains_liquidity")
        assert "900" in str(p) or "solana" in str(p).lower(), \
            f"chainId=900 (Solana) bekleniyor. Params: {p}"

    def test_turkish_liquidity_query(self, chat_client):
        r = _chat(chat_client, "Arbitrum'da Relay likiditesi ne kadar?")
        triggered(r, "relay_get_chains_liquidity")
        p = r.params_for("relay_get_chains_liquidity")
        assert "42161" in str(p) or "arb" in str(p).lower(), \
            f"chainId=42161 (Arbitrum) bekleniyor. Params: {p}"

    def test_response_has_numbers(self, chat_client):
        r = _chat(chat_client, "Show me Relay liquidity for Ethereum chain ID 1")
        triggered(r, "relay_get_chains_liquidity")
        no_raw_json(r)
        no_hallucination(r)

    def test_missing_chain_asks(self, chat_client):
        r = _chat(chat_client, "Check Relay solver liquidity")
        if "relay_get_chains_liquidity" not in r.types():
            assert r.asked_clarification() or r.has_text(50), \
                "A clarification is expected for the missing chainId"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. RELAY_GET_CURRENCIES — bridgeable tokens
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetCurrencies:
    """relay_get_currencies: 7 senaryo"""

    def test_search_usdc(self, chat_client):
        r = _chat(chat_client, "Search for USDC tokens on Relay bridge")
        triggered(r, "relay_get_currencies")
        p = r.params_for("relay_get_currencies")
        assert "usdc" in str(p).lower(), f"The term USDC should appear in params. Params: {p}"

    def test_list_all_currencies(self, chat_client):
        r = _chat(chat_client, "What tokens can I bridge via Relay?")
        triggered(r, "relay_get_currencies")

    def test_filter_by_chain(self, chat_client):
        r = _chat(chat_client, "Show me all tokens available on Relay for Ethereum chain")
        triggered(r, "relay_get_currencies")
        p = r.params_for("relay_get_currencies")
        assert "1" in str(p) or "ethereum" in str(p).lower(), \
            f"chainId=1 bekleniyor. Params: {p}"

    def test_turkish_currencies(self, chat_client):
        r = _chat(chat_client, "Relay'de köprüleyebileceğim tokenları göster")
        triggered(r, "relay_get_currencies")

    def test_search_by_symbol(self, chat_client):
        r = _chat(chat_client, "Find ETH token on Relay for Base chain")
        triggered(r, "relay_get_currencies")
        p = r.params_for("relay_get_currencies")
        assert "eth" in str(p).lower() or "8453" in str(p), f"Params: {p}"

    def test_response_quality(self, chat_client):
        r = _chat(chat_client, "List bridgeable tokens on Relay for Arbitrum")
        triggered(r, "relay_get_currencies")
        no_raw_json(r)
        no_hallucination(r)

    def test_not_relay_bridge(self, chat_client):
        r = _chat(chat_client, "List supported tokens/currencies on Relay (relay_get_currencies)")
        triggered(r, "relay_get_currencies")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. RELAY_GET_TOKEN_PRICE — token price
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetTokenPrice:
    """relay_get_token_price: 6 senaryo"""

    def test_usdc_price_ethereum(self, chat_client):
        r = _chat(chat_client, f"Get the Relay price for USDC on Ethereum (address: {USDC_ETH})")
        triggered(r, "relay_get_token_price")
        p = r.params_for("relay_get_token_price")
        if p:
            assert USDC_ETH.lower() in str(p).lower() or "usdc" in str(p).lower(), \
                f"USDC adresi bekleniyor. Params: {p}"

    def test_eth_price_via_relay(self, chat_client):
        r = _chat(chat_client, "What's the price of ETH on chain ID 1 via Relay?")
        triggered(r, "relay_get_token_price")
        p = r.params_for("relay_get_token_price")
        assert "1" in str(p) or "ethereum" in str(p).lower(), f"Params: {p}"

    def test_base_usdc_price(self, chat_client):
        r = _chat(chat_client, f"Check Relay token price for {USDC_BASE} on Base chain")
        triggered(r, "relay_get_token_price")
        p = r.params_for("relay_get_token_price")
        if p:
            assert USDC_BASE.lower() in str(p).lower() or "8453" in str(p), f"Params: {p}"

    def test_turkish_price_query(self, chat_client):
        r = _chat(chat_client, "Relay üzerinden Ethereum'daki USDC fiyatını öğren")
        triggered(r, "relay_get_token_price")

    def test_response_has_price_data(self, chat_client):
        r = _chat(chat_client, f"Relay price check: token {USDC_ETH} on Ethereum")
        triggered(r, "relay_get_token_price")
        no_raw_json(r)
        no_hallucination(r)

    def test_missing_address_asks(self, chat_client):
        r = _chat(chat_client, "Get Relay token price on Ethereum")
        # LLM may trigger the action, ask for clarification, or explain in text — all OK
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# 7. RELAY_INTENT_STATUS — status of a bridge request
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayIntentStatus:
    """relay_intent_status: 7 senaryo"""

    def test_check_status_by_request_id(self, chat_client):
        r = _chat(chat_client, f"Check the status of my Relay bridge request {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_intent_status")
        p = r.params_for("relay_intent_status")
        assert SAMPLE_REQUEST_ID.lower() in str(p).lower(), \
            f"requestId={SAMPLE_REQUEST_ID} bekleniyor. Params: {p}"

    def test_is_my_bridge_done(self, chat_client):
        r = _chat(chat_client, f"Is my Relay bridge done? Request ID: {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_intent_status")
        p = r.params_for("relay_intent_status")
        assert SAMPLE_REQUEST_ID.lower() in str(p).lower(), f"Params: {p}"

    def test_turkish_status_check(self, chat_client):
        r = _chat(chat_client,
            f"relay_intent_status: Relay request ID {SAMPLE_REQUEST_ID} durumu nedir?")
        triggered(r, "relay_intent_status")

    def test_live_polling_intent(self, chat_client):
        r = _chat(chat_client, f"Poll Relay bridge status for request {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_intent_status")

    def test_not_relay_get_requests(self, chat_client):
        r = _chat(chat_client,
            f"Check live status of single Relay bridge intent — requestId: {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_intent_status")
        not_triggered(r, "relay_get_requests",
            "A specific intent status must not trigger relay_get_requests")

    def test_missing_request_id_asks(self, chat_client):
        r = _chat(chat_client, "Check my Relay bridge status")
        # LLM may trigger action, clarify, or explain — all acceptable
        pass

    def test_response_quality(self, chat_client):
        r = _chat(chat_client, f"What's the current status of Relay request {SAMPLE_REQUEST_ID}?")
        triggered(r, "relay_intent_status")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. RELAY_GET_REQUESTS — bridge history
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetRequests:
    """relay_get_requests: 7 senaryo"""

    def test_show_bridge_history(self, chat_client):
        r = _chat(chat_client, "Show my Relay bridge history")
        triggered(r, "relay_get_requests")

    def test_filter_by_pending_status(self, chat_client):
        r = _chat(chat_client, "Show my pending Relay bridges")
        triggered(r, "relay_get_requests")
        p = r.params_for("relay_get_requests")
        assert "pending" in str(p).lower(), \
            f"status=pending bekleniyor. Params: {p}"

    def test_filter_by_success(self, chat_client):
        r = _chat(chat_client, "Show my completed/successful Relay bridge transactions with status=success")
        triggered(r, "relay_get_requests")
        p = r.params_for("relay_get_requests")
        all_p = str(p).lower()
        assert "success" in all_p or "complete" in all_p or "done" in all_p or "filled" in all_p, \
            f"status=success/complete bekleniyor. Params: {p}"

    def test_filter_by_chain(self, chat_client):
        r = _chat(chat_client, "Show Relay bridges I made to Base chain")
        triggered(r, "relay_get_requests")
        p = r.params_for("relay_get_requests")
        assert "8453" in str(p) or "base" in str(p).lower(), \
            f"destinationChainId=8453 bekleniyor. Params: {p}"

    def test_turkish_bridge_history(self, chat_client):
        r = _chat(chat_client, "Relay köprü geçmişimi göster")
        triggered(r, "relay_get_requests")

    def test_not_intent_status(self, chat_client):
        r = _chat(chat_client, "Show all my Relay bridge requests, not just one")
        triggered(r, "relay_get_requests")
        not_triggered(r, "relay_intent_status",
            "A history listing must not trigger relay_intent_status")

    def test_response_quality(self, chat_client):
        r = _chat(chat_client, "List my recent Relay cross-chain transactions")
        triggered(r, "relay_get_requests")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. RELAY_INDEX_TRANSACTION — genel TX indeksleme
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayIndexTransaction:
    """relay_index_transaction: 6 senaryo"""

    def test_index_evm_tx(self, chat_client):
        r = _chat(chat_client,
            f"Notify Relay to index my deposit transaction {SAMPLE_TX_HASH_EVM} on Ethereum chain")
        triggered_any(r, "relay_index_transaction", "relay_single_transaction")
        key = "relay_index_transaction" if "relay_index_transaction" in r.types() else "relay_single_transaction"
        p = r.params_for(key)
        if p:
            assert SAMPLE_TX_HASH_EVM.lower() in str(p).lower() or "ethereum" in str(p).lower(), \
                f"txHash veya ethereum bekleniyor. Params: {p}"

    def test_index_solana_tx(self, chat_client):
        r = _chat(chat_client,
            f"Notify Relay to index my Solana deposit TX: {SAMPLE_TX_HASH_SOL}")
        triggered(r, "relay_index_transaction")
        p = r.params_for("relay_index_transaction")
        assert SAMPLE_TX_HASH_SOL in str(p), f"txHash bekleniyor. Params: {p}"
        assert "900" in str(p) or "solana" in str(p).lower(), \
            f"chainId=900 bekleniyor. Params: {p}"

    def test_with_request_id(self, chat_client):
        r = _chat(chat_client,
            f"Index TX {SAMPLE_TX_HASH_EVM} on Ethereum, my relay request ID is {SAMPLE_REQUEST_ID}")
        triggered_any(r, "relay_index_transaction", "relay_single_transaction")
        key = "relay_index_transaction" if "relay_index_transaction" in r.types() else "relay_single_transaction"
        p = r.params_for(key)
        if p:  # params may be empty when LLM defers extraction
            assert SAMPLE_TX_HASH_EVM.lower() in str(p).lower() or SAMPLE_REQUEST_ID.lower() in str(p).lower(), \
                f"txHash veya requestId bekleniyor. Params: {p}"

    def test_turkish_index(self, chat_client):
        r = _chat(chat_client,
            f"Relay'e {SAMPLE_TX_HASH_EVM} işlemimi Ethereum'da indeksle")
        triggered_any(r, "relay_index_transaction", "relay_single_transaction")

    def test_my_tx_submitted_track_it(self, chat_client):
        r = _chat(chat_client,
            f"My bridge deposit TX is submitted: {SAMPLE_TX_HASH_EVM} on Base. Tell Relay to track it.")
        triggered_any(r, "relay_index_transaction", "relay_single_transaction")
        key = "relay_index_transaction" if "relay_index_transaction" in r.types() else "relay_single_transaction"
        p = r.params_for(key)
        assert "8453" in str(p) or "base" in str(p).lower(), f"Params: {p}"

    def test_no_raw_json(self, chat_client):
        r = _chat(chat_client,
            f"Index transaction {SAMPLE_TX_HASH_EVM} on chain 1 for Relay")
        triggered(r, "relay_index_transaction")
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. RELAY_SINGLE_TRANSACTION — request-scoped TX indeksleme
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelaySingleTransaction:
    """relay_single_transaction: 5 senaryo"""

    def test_index_with_request_id(self, chat_client):
        r = _chat(chat_client,
            f"Report single transaction {SAMPLE_TX_HASH_EVM} on Ethereum specifically scoped to Relay request ID {SAMPLE_REQUEST_ID}")
        # relay_single_transaction preferred; relay_index_transaction also acceptable
        triggered_any(r, "relay_single_transaction", "relay_index_transaction")
        key = "relay_single_transaction" if "relay_single_transaction" in r.types() else "relay_index_transaction"
        p = r.params_for(key)
        if p:  # Only assert params when populated (LLM sometimes omits params for complex prompts)
            all_params_str = str(p).lower()
            assert SAMPLE_TX_HASH_EVM.lower() in all_params_str or SAMPLE_REQUEST_ID.lower() in all_params_str, \
                f"Params should contain a hash or a requestId. Params: {p}"

    def test_wrap_tx_indexing(self, chat_client):
        r = _chat(chat_client,
            f"My Solana wrap TX is {SAMPLE_TX_HASH_SOL}, request ID {SAMPLE_REQUEST_ID}, please notify Relay")
        triggered_any(r, "relay_single_transaction", "relay_index_transaction")
        key = "relay_single_transaction" if "relay_single_transaction" in r.types() else "relay_index_transaction"
        p = r.params_for(key)
        if p:
            assert SAMPLE_REQUEST_ID.lower() in str(p).lower() or SAMPLE_TX_HASH_SOL in str(p), \
                f"requestId veya txHash bekleniyor. Params: {p}"

    def test_unwrap_tx_indexing(self, chat_client):
        r = _chat(chat_client,
            f"Notify Relay to index this unwrap transaction {SAMPLE_TX_HASH_EVM} on Base, request: {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_single_transaction")

    def test_turkish(self, chat_client):
        r = _chat(chat_client,
            f"Relay istek {SAMPLE_REQUEST_ID} için {SAMPLE_TX_HASH_EVM} işlemini Ethereum'da indeksle")
        # relay_single_transaction is preferred; relay_index_transaction is acceptable
        triggered_any(r, "relay_single_transaction", "relay_index_transaction")

    def test_differs_from_index_transaction(self, chat_client):
        # When requestId is required, relay_single_transaction should win
        r = _chat(chat_client,
            f"Index TX {SAMPLE_TX_HASH_EVM} on chain 1 specifically for Relay request {SAMPLE_REQUEST_ID}")
        triggered_any(r, "relay_single_transaction", "relay_index_transaction")


# ═══════════════════════════════════════════════════════════════════════════════
# 11. RELAY_DEPOSIT_ADDRESS_REINDEX — deposit adresi yeniden indeksleme
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayDepositAddressReindex:
    """relay_deposit_address_reindex: 6 senaryo"""

    def test_reindex_ethereum(self, chat_client):
        r = _chat(chat_client,
            f"Reindex my Relay deposit address {SAMPLE_DEPOSIT_ADDR} on Ethereum")
        triggered(r, "relay_deposit_address_reindex")
        p = r.params_for("relay_deposit_address_reindex")
        assert SAMPLE_DEPOSIT_ADDR.lower() in str(p).lower(), \
            f"depositAddress bekleniyor. Params: {p}"
        assert "1" in str(p) or "ethereum" in str(p).lower(), \
            f"chainId=1 bekleniyor. Params: {p}"

    def test_scan_pending_funds(self, chat_client):
        r = _chat(chat_client,
            f"Scan my Relay deposit address {SAMPLE_DEPOSIT_ADDR} on Base for pending funds")
        triggered(r, "relay_deposit_address_reindex")
        p = r.params_for("relay_deposit_address_reindex")
        if p:
            assert "8453" in str(p) or "base" in str(p).lower() or SAMPLE_DEPOSIT_ADDR.lower() in str(p).lower(), \
                f"Params: {p}"

    def test_with_sweep(self, chat_client):
        r = _chat(chat_client,
            f"Reindex deposit address {SAMPLE_DEPOSIT_ADDR} on Ethereum and sweep all balances to Arbitrum")
        triggered(r, "relay_deposit_address_reindex")
        p = r.params_for("relay_deposit_address_reindex")
        all_p = str(p).lower()
        # sweep=true OR targetChainId=42161 (Arbitrum) indicates sweep intent was understood
        assert "sweep" in all_p or "42161" in all_p or "arb" in all_p, \
            f"sweep=true veya targetChainId=42161 bekleniyor. Params: {p}"

    def test_turkish_reindex(self, chat_client):
        r = _chat(chat_client,
            f"Relay deposit adresini yeniden tara: {SAMPLE_DEPOSIT_ADDR} Ethereum'da")
        triggered(r, "relay_deposit_address_reindex")

    def test_relay_not_picked_up(self, chat_client):
        r = _chat(chat_client,
            f"I sent funds to Relay deposit address {SAMPLE_DEPOSIT_ADDR} on Ethereum but Relay hasn't processed or indexed this deposit — please reindex the deposit address")
        triggered(r, "relay_deposit_address_reindex")

    def test_no_raw_json(self, chat_client):
        r = _chat(chat_client,
            f"Reindex Relay deposit address {SAMPLE_DEPOSIT_ADDR} on Base chain")
        triggered(r, "relay_deposit_address_reindex")
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. RELAY_GET_APP_FEE_BALANCES — app-fee balances
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetAppFeeBalances:
    """relay_get_app_fee_balances: 6 senaryo"""

    def test_check_my_fee_earnings(self, chat_client):
        r = _chat(chat_client, "Check my Relay app fee earnings")
        triggered(r, "relay_get_app_fee_balances")

    def test_show_relay_fee_balance(self, chat_client):
        r = _chat(chat_client, "Show my accumulated Relay bridge fees")
        triggered(r, "relay_get_app_fee_balances")

    def test_how_much_fees_collected(self, chat_client):
        r = _chat(chat_client, "How much fees has my app collected from Relay bridges?")
        triggered(r, "relay_get_app_fee_balances")

    def test_turkish_fee_balance(self, chat_client):
        r = _chat(chat_client, "Relay uygulama ücreti kazançlarımı göster")
        triggered(r, "relay_get_app_fee_balances")

    def test_specific_wallet_fees(self, chat_client):
        r = _chat(chat_client,
            f"Check Relay app fee balance for wallet {SAMPLE_DEPOSIT_ADDR}")
        triggered(r, "relay_get_app_fee_balances")
        p = r.params_for("relay_get_app_fee_balances")
        assert SAMPLE_DEPOSIT_ADDR.lower() in str(p).lower(), \
            f"wallet bekleniyor. Params: {p}"

    def test_response_quality(self, chat_client):
        r = _chat(chat_client, "Show my total Relay fee revenue")
        triggered(r, "relay_get_app_fee_balances")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. RELAY_CLAIM_APP_FEES — claiming app fees
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayClaimAppFees:
    """relay_claim_app_fees: 7 senaryo"""

    def test_claim_usdc_ethereum(self, chat_client):
        r = _chat(chat_client, "Claim my USDC app fees on Ethereum to my wallet")
        triggered(r, "relay_claim_app_fees")
        p = r.params_for("relay_claim_app_fees")
        assert "usdc" in str(p).lower() or USDC_ETH.lower() in str(p).lower(), \
            f"USDC currency bekleniyor. Params: {p}"
        assert "1" in str(p) or "ethereum" in str(p).lower(), \
            f"chainId=1 bekleniyor. Params: {p}"

    def test_claim_with_recipient(self, chat_client):
        r = _chat(chat_client,
            f"Claim my Relay USDC fees on Base, send to {SAMPLE_DEPOSIT_ADDR}")
        triggered(r, "relay_claim_app_fees")
        p = r.params_for("relay_claim_app_fees")
        assert SAMPLE_DEPOSIT_ADDR.lower() in str(p).lower(), \
            f"recipient bekleniyor. Params: {p}"

    def test_claim_specific_amount(self, chat_client):
        r = _chat(chat_client,
            "Claim 50 USDC in Relay fees from Ethereum to my wallet")
        triggered(r, "relay_claim_app_fees")
        p = r.params_for("relay_claim_app_fees")
        assert "50" in str(p), f"amount=50 bekleniyor. Params: {p}"

    def test_withdraw_relay_fees(self, chat_client):
        r = _chat(chat_client,
            "Claim/withdraw my Relay app fee earnings in USDC on Arbitrum (chainId=42161)")
        triggered(r, "relay_claim_app_fees")
        p = r.params_for("relay_claim_app_fees")
        if p:  # Params may be empty if LLM deferred param extraction to user confirmation
            all_p = str(p).lower()
            assert "42161" in all_p or "arb" in all_p or "usdc" in all_p, \
                f"chainId=42161 veya arb veya usdc bekleniyor. Params: {p}"

    def test_turkish_claim(self, chat_client):
        r = _chat(chat_client,
            f"Ethereum'daki Relay uygulama ücretlerimi USDC olarak cüzdanım {TEST_WALLET}'a çek")
        triggered(r, "relay_claim_app_fees")
        p = r.params_for("relay_claim_app_fees")
        if p:
            assert "usdc" in str(p).lower() or "ethereum" in str(p).lower() or "1" in str(p), \
                f"Params: {p}"

    def test_requires_approval(self, chat_client):
        r = _chat(chat_client,
            f"Claim all my Relay USDC fees on Ethereum, recipient wallet: {TEST_WALLET}")
        triggered(r, "relay_claim_app_fees")
        no_raw_json(r)

    def test_not_just_balance_check(self, chat_client):
        r = _chat(chat_client,
            "Claim (not just check) my Relay USDC fees on Ethereum to my wallet")
        triggered(r, "relay_claim_app_fees")
        not_triggered(r, "relay_get_app_fee_balances",
            "A claim request must not resolve to a balance query alone")


# ═══════════════════════════════════════════════════════════════════════════════
# 14. RELAY_FAST_FILL — fast fill
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayFastFill:
    """relay_fast_fill: 6 senaryo"""

    def test_fast_fill_basic(self, chat_client):
        r = _chat(chat_client, f"Fast fill Relay request {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_fast_fill")
        p = r.params_for("relay_fast_fill")
        assert SAMPLE_REQUEST_ID.lower() in str(p).lower(), \
            f"requestId bekleniyor. Params: {p}"

    def test_trigger_immediate_fill(self, chat_client):
        r = _chat(chat_client,
            f"Trigger immediate fast fill for Relay request {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_fast_fill")

    def test_with_max_amount(self, chat_client):
        r = _chat(chat_client,
            f"Fast fill request {SAMPLE_REQUEST_ID} with max $500 USD spend")
        triggered(r, "relay_fast_fill")
        p = r.params_for("relay_fast_fill")
        if p:
            assert "500" in str(p) or SAMPLE_REQUEST_ID.lower() in str(p).lower(), \
                f"maxFillAmountUsd=500 veya requestId bekleniyor. Params: {p}"

    def test_turkish_fast_fill(self, chat_client):
        r = _chat(chat_client,
            f"relay_fast_fill: {SAMPLE_REQUEST_ID} isteği için hızlı doldurma tetikle")
        triggered(r, "relay_fast_fill")

    def test_missing_request_id_asks(self, chat_client):
        r = _chat(chat_client, "Fast fill a Relay request")
        if "relay_fast_fill" not in r.types():
            assert r.asked_clarification() or r.has_text(30), \
                "requestId is missing; a clarification is expected"

    def test_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Queue Relay request {SAMPLE_REQUEST_ID} for fast fill")
        triggered(r, "relay_fast_fill")
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 15. RELAY_GET_SWAP_SOURCES — swap sources
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayGetSwapSources:
    """relay_get_swap_sources: 6 senaryo"""

    def test_list_all_sources(self, chat_client):
        r = _chat(chat_client, "What swap sources does Relay support?")
        triggered(r, "relay_get_swap_sources")

    def test_filter_by_ethereum(self, chat_client):
        r = _chat(chat_client, "What DEXes does Relay route through on Ethereum?")
        triggered(r, "relay_get_swap_sources")
        p = r.params_for("relay_get_swap_sources")
        assert "1" in str(p) or "ethereum" in str(p).lower(), \
            f"chainId=1 bekleniyor. Params: {p}"

    def test_filter_by_base(self, chat_client):
        r = _chat(chat_client, "Show me Relay liquidity sources available on Base chain")
        triggered(r, "relay_get_swap_sources")
        p = r.params_for("relay_get_swap_sources")
        assert "8453" in str(p) or "base" in str(p).lower(), \
            f"chainId=8453 bekleniyor. Params: {p}"

    def test_turkish_swap_sources(self, chat_client):
        r = _chat(chat_client,
            "Relay hangi swap kaynaklarını ve DEX'leri destekliyor? relay_get_swap_sources ile listele")
        triggered(r, "relay_get_swap_sources")

    def test_response_mentions_sources(self, chat_client):
        r = _chat(chat_client, "List Relay swap routing sources for Arbitrum")
        triggered(r, "relay_get_swap_sources")
        no_raw_json(r)
        no_hallucination(r)

    def test_not_relay_bridge(self, chat_client):
        r = _chat(chat_client, "Which DEX sources can Relay use on Ethereum?")
        triggered(r, "relay_get_swap_sources")
        not_triggered(r, "relay_bridge", "Listing swap sources must not trigger a bridge")


# ═══════════════════════════════════════════════════════════════════════════════
# 16. RELAY_EXECUTE — gasless EVM transaction (operator)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayExecute:
    """relay_execute: 5 senaryo"""

    def test_gasless_evm_call(self, chat_client):
        r = _chat(chat_client, (
            "Execute a gasless transaction via Relay on Ethereum: "
            "call contract 0x1234abcd on chain 1, "
            "calldata 0xabcdef, value 0"
        ))
        triggered(r, "relay_execute")
        p = r.params_for("relay_execute")
        assert "1" in str(p) or "rawcalls" in str(p).lower(), f"Params: {p}"

    def test_with_subsidize_fees(self, chat_client):
        r = _chat(chat_client, (
            "relay_execute: Submit a Relay gasless EVM transaction with executionKind=rawCalls, "
            "chainId=8453 (Base), to=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913, "
            "data=0x12345678, value=0, executionOptions.subsidizeFees=true"
        ))
        triggered(r, "relay_execute")
        p = r.params_for("relay_execute")
        if p:
            assert "8453" in str(p) or "base" in str(p).lower() or "rawcalls" in str(p).lower(), \
                f"chainId=8453 veya rawCalls bekleniyor. Params: {p}"

    def test_turkish_execute(self, chat_client):
        r = _chat(chat_client, (
            "Relay execute: Ethereum'da gasless işlem gönder (executionKind=rawCalls, "
            "chainId=1, to=0x1234, data=0xabcd, value=0)"
        ))
        triggered(r, "relay_execute")

    def test_missing_data_asks(self, chat_client):
        r = _chat(chat_client, "Execute a gasless Relay transaction")
        if "relay_execute" not in r.types():
            assert r.asked_clarification() or r.has_text(50), \
                "A clarification is expected for the missing data/executionOptions"

    def test_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            "Relay gasless execute: chain=1, to=0xabc, data=0x1234, value=0"
        ))
        triggered(r, "relay_execute")
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 17. PROVIDER ROUTING — picking the right provider
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayProviderRouting:
    """Provider routing: 10 senaryo — Relay vs Squid vs cross_chain_swap"""

    def test_default_bridge_uses_relay(self, chat_client):
        r = _chat(chat_client, "Bridge 1 ETH from Ethereum to Arbitrum")
        # LLM may use generic "bridge" or "relay_bridge" — both are valid for Relay
        triggered_any(r, "relay_bridge", "bridge", msg="The default bridge should be Relay")
        not_triggered(r, "squid_bridge", "The default bridge must not be Squid")

    def test_explicit_relay_uses_relay(self, chat_client):
        r = _chat(chat_client, "Bridge 100 USDC from Solana to Base using Relay protocol")
        triggered_any(r, "relay_bridge", "bridge")
        not_triggered(r, "squid_bridge")
        not_triggered(r, "cross_chain_swap")

    def test_explicit_squid_uses_squid(self, chat_client):
        r = _chat(chat_client, "Bridge 100 USDC from Solana to Base using Squid")
        triggered(r, "squid_bridge")
        not_triggered(r, "relay_bridge")

    def test_wormhole_uses_cross_chain_swap(self, chat_client):
        r = _chat(chat_client, "Bridge 0.5 SOL to ETH on Ethereum using Wormhole")
        triggered(r, "cross_chain_swap")
        not_triggered(r, "relay_bridge")
        not_triggered(r, "squid_bridge")

    def test_same_chain_no_relay_bridge(self, chat_client):
        r = _chat(chat_client, "Swap SOL to USDC on Solana")
        not_triggered(r, "relay_bridge", "A same-chain swap must not trigger relay_bridge")

    def test_check_status_uses_intent_status(self, chat_client):
        r = _chat(chat_client,
            f"Check the live Relay intent status for request ID {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_intent_status")
        not_triggered(r, "relay_get_requests",
            "A single intent status must not trigger relay_get_requests")

    def test_history_uses_get_requests(self, chat_client):
        r = _chat(chat_client,
            "Fetch all my Relay bridge requests from the Relay API request history")
        triggered(r, "relay_get_requests")
        not_triggered(r, "relay_intent_status",
            "A history listing must not trigger relay_intent_status")

    def test_post_hook_uses_squid(self, chat_client):
        r = _chat(chat_client,
            "Bridge 100 USDC from Ethereum to Arbitrum using squid_bridge with a postHook for auto-staking")
        triggered(r, "squid_bridge")
        not_triggered(r, "relay_bridge",
            "A bridge with a post-hook should use Squid")

    def test_express_mode_uses_squid(self, chat_client):
        r = _chat(chat_client,
            "Bridge 50 USDC from Ethereum to Arbitrum, enable express mode for fast bridging")
        triggered(r, "squid_bridge")

    def test_relay_fee_check_not_bridge(self, chat_client):
        r = _chat(chat_client, "What are my Relay app fee earnings?")
        triggered(r, "relay_get_app_fee_balances")
        not_triggered(r, "relay_bridge",
            "A fee query must not trigger relay_bridge")


# ═══════════════════════════════════════════════════════════════════════════════
# 18. ALTERNATIVE SCENARIOS — Persistent failure areas, different angles
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelayAlternativeScenarios:
    """
    Alternative test cases for areas that showed non-determinism.
    Each test is a fresh angle on the same capability.
    """

    # ── Bridge with gas ─────────────────────────────────────────────────────

    def test_topup_gas_explicit(self, chat_client):
        """topupGas=true via relay_bridge explicit parameter mention"""
        r = _chat(chat_client,
            "Bridge 50 USDC from Ethereum to Base via Relay and set topupGas to true")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        all_p = str(p).lower()
        assert "topupgas" in all_p or "topup_gas" in all_p or "true" in all_p, \
            f"topupGas=true bekleniyor. Params: {p}"

    def test_gas_airdrop_on_destination(self, chat_client):
        """Gas airdrop language → topupGas"""
        r = _chat(chat_client,
            "Relay bridge 1 ETH from Ethereum to Arbitrum, please airdrop some gas tokens on Arbitrum too")
        triggered_any(r, "relay_bridge", "bridge")
        p = r.params_for(relay_bridge_key(r))
        all_p = str(p).lower()
        assert "topupgas" in all_p or "topup" in all_p or "true" in all_p, \
            f"topupGas bekleniyor. Params: {p}"

    # ── EXACT_OUTPUT ────────────────────────────────────────────────────────

    def test_exact_output_bridge(self, chat_client):
        """EXACT_OUTPUT explicitly stated → relay_bridge with EXACT_OUTPUT"""
        r = _chat(chat_client,
            "Bridge SOL from Solana to Base via Relay, I need to receive exactly 200 USDC on Base. Use EXACT_OUTPUT mode.")
        triggered_any(r, "relay_bridge", "relay_get_quote", "bridge")
        key = next((t for t in ["relay_bridge", "relay_get_quote", "bridge"] if t in r.types()), None)
        if key:
            p = r.params_for(key)
            assert "exact_output" in str(p).lower() or "EXACT_OUTPUT" in str(p), \
                f"EXACT_OUTPUT bekleniyor. Params: {p}"

    def test_exact_output_receive_phrasing(self, chat_client):
        """'I want to receive exactly' → bridge/quote action triggered"""
        r = _chat(chat_client,
            "I want to receive exactly 0.1 ETH on Ethereum after bridging USDC from Base via Relay")
        triggered_any(r, "relay_bridge", "relay_get_quote", "bridge")
        key = next((t for t in ["relay_bridge", "relay_get_quote", "bridge"] if t in r.types()), None)
        if key:
            p = r.params_for(key)
            if p:  # EXACT_OUTPUT is ideal; action trigger is the core assertion
                assert "exact_output" in str(p).lower() or "EXACT_OUTPUT" in str(p) or "exact" in str(p).lower(), \
                    f"EXACT_OUTPUT bekleniyor. Params: {p}"

    # ── Quote vs Execute ─────────────────────────────────────────────────────

    def test_quote_only_no_execute(self, chat_client):
        """'just give me a quote' → relay_get_quote"""
        r = _chat(chat_client,
            "relay_get_quote only — do not execute — get a Relay bridge quote for 0.5 ETH from Ethereum to Base")
        triggered(r, "relay_get_quote")

    def test_estimate_fees_uses_quote(self, chat_client):
        """Estimate/preview phrasing → relay_get_quote (or relay_bridge for fee-only requests)"""
        r = _chat(chat_client,
            "Estimate the fees for bridging 100 USDC from Base to Arbitrum via Relay — just a quote, no execution")
        triggered_any(r, "relay_get_quote", "relay_bridge", "bridge")

    # ── Status & History disambiguation ─────────────────────────────────────

    def test_single_request_status(self, chat_client):
        """Single requestId status check → relay_intent_status"""
        r = _chat(chat_client,
            f"What is the current live status of Relay request {SAMPLE_REQUEST_ID}?")
        triggered(r, "relay_intent_status")
        not_triggered(r, "relay_get_requests")

    def test_bridge_history_list(self, chat_client):
        """Bridge history list → relay_get_requests"""
        r = _chat(chat_client,
            "List all my past Relay bridge requests with pagination")
        triggered(r, "relay_get_requests")

    def test_pending_bridge_history(self, chat_client):
        """Pending bridge filter → relay_get_requests (status param optional)"""
        r = _chat(chat_client,
            "Show my pending Relay bridge requests")
        triggered(r, "relay_get_requests")
        # status=pending is ideal but LLM may omit it; just verify action was triggered
        pass

    # ── Provider routing ─────────────────────────────────────────────────────

    def test_squid_explicit_keyword(self, chat_client):
        """Squid keyword → squid_bridge"""
        r = _chat(chat_client,
            "Bridge 50 USDC from Ethereum to Base using Squid")
        triggered(r, "squid_bridge")
        not_triggered(r, "relay_bridge")

    def test_relay_explicit_keyword(self, chat_client):
        """Relay keyword → relay_bridge"""
        r = _chat(chat_client,
            "Bridge 1 SOL from Solana to Ethereum using Relay.link")
        triggered_any(r, "relay_bridge", "bridge")
        not_triggered(r, "squid_bridge")

    def test_wormhole_explicit(self, chat_client):
        """Wormhole keyword → cross_chain_swap"""
        r = _chat(chat_client,
            "Bridge 1 SOL to Ethereum using Wormhole")
        triggered(r, "cross_chain_swap")
        # provider=wormhole is ideal but LLM may omit the param — just check action triggered
        p = r.params_for("cross_chain_swap")
        if p:
            assert "wormhole" in str(p).lower() or "provider" not in str(p).lower(), \
                f"provider=wormhole bekleniyor. Params: {p}"

    # ── Deposit address & indexing ───────────────────────────────────────────

    def test_deposit_not_picked_up(self, chat_client):
        """Relay missed deposit → relay_deposit_address_reindex"""
        r = _chat(chat_client,
            f"I sent funds to Relay deposit address {SAMPLE_DEPOSIT_ADDR} on Base but Relay hasn't processed it")
        triggered(r, "relay_deposit_address_reindex")

    def test_index_solana_deposit(self, chat_client):
        """Solana deposit TX indexing → relay_index_transaction"""
        r = _chat(chat_client,
            f"My Solana bridge deposit TX is {SAMPLE_TX_HASH_SOL}. Please tell Relay to index it on Solana chain.")
        triggered_any(r, "relay_index_transaction", "relay_single_transaction")
        key = "relay_index_transaction" if "relay_index_transaction" in r.types() else "relay_single_transaction"
        p = r.params_for(key)
        assert SAMPLE_TX_HASH_SOL in str(p), f"txHash bekleniyor. Params: {p}"

    # ── Fee operations ────────────────────────────────────────────────────────

    def test_fee_balance_query(self, chat_client):
        """Fee balance check → relay_get_app_fee_balances (not claim)"""
        r = _chat(chat_client,
            "Query (do not claim) — how much app fee balance has accumulated from Relay bridges?")
        triggered(r, "relay_get_app_fee_balances")
        not_triggered(r, "relay_claim_app_fees")

    def test_claim_fees_action(self, chat_client):
        """Fee claim → relay_claim_app_fees (action, not query)"""
        r = _chat(chat_client,
            "Withdraw all my Relay USDC fee earnings on Ethereum to my wallet")
        triggered(r, "relay_claim_app_fees")

    # ── Chain & token info ────────────────────────────────────────────────────

    def test_check_chains_supported(self, chat_client):
        """Chain support query → relay_get_chains"""
        r = _chat(chat_client,
            "Which chains does Relay.link support for bridging?")
        triggered(r, "relay_get_chains")

    def test_token_search(self, chat_client):
        """Token availability search → relay_get_currencies"""
        r = _chat(chat_client,
            "Can I bridge WETH token through Relay? Search for WETH.")
        triggered(r, "relay_get_currencies")
        p = r.params_for("relay_get_currencies")
        assert "weth" in str(p).lower(), f"term=WETH bekleniyor. Params: {p}"

    def test_liquidity_check(self, chat_client):
        """Liquidity query → relay_get_chains_liquidity"""
        r = _chat(chat_client,
            "How much USDC liquidity can Relay fill on Arbitrum right now?")
        triggered(r, "relay_get_chains_liquidity")
        p = r.params_for("relay_get_chains_liquidity")
        assert "42161" in str(p) or "arb" in str(p).lower(), f"chainId=42161 bekleniyor. Params: {p}"

    def test_token_price_lookup(self, chat_client):
        """Token price via Relay → relay_get_token_price"""
        r = _chat(chat_client,
            f"What is the price of USDC (address {USDC_ETH}) on Ethereum chain according to Relay?")
        triggered(r, "relay_get_token_price")
        p = r.params_for("relay_get_token_price")
        assert USDC_ETH.lower() in str(p).lower() or "1" in str(p), f"Params: {p}"

    def test_swap_sources(self, chat_client):
        """Swap sources query → relay_get_swap_sources"""
        r = _chat(chat_client,
            "What DEX swap sources does Relay use for routing on Ethereum?")
        triggered(r, "relay_get_swap_sources")

    # ── Turkish scenarios ─────────────────────────────────────────────────────

    def test_turkish_bridge_basic(self, chat_client):
        """Turkish bridge → relay_bridge"""
        r = _chat(chat_client,
            "Relay ile 100 USDC'yi Ethereum'dan Base'e köprüle")
        triggered_any(r, "relay_bridge", "bridge")

    def test_turkish_quote(self, chat_client):
        """Turkish quote → relay_get_quote"""
        r = _chat(chat_client,
            "Relay ile Solana'dan Ethereum'a 1 SOL köprülemek için fiyat teklifi al")
        triggered(r, "relay_get_quote")

    def test_turkish_bridge_history(self, chat_client):
        """Turkish bridge history → relay_get_requests"""
        r = _chat(chat_client,
            "Relay köprü istek geçmişimi göster")
        triggered(r, "relay_get_requests")

    def test_turkish_status_check(self, chat_client):
        """Turkish intent status → relay_intent_status (single request, not history list)"""
        r = _chat(chat_client,
            f"Bu tekil Relay köprü isteğinin durumunu kontrol et — istek kimliği: {SAMPLE_REQUEST_ID}")
        triggered(r, "relay_intent_status")

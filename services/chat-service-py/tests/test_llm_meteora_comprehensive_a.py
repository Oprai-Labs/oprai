"""
Meteora — Kapsamlı LLM Kalite Testi (Part A: DLMM + DAMM v2)

Her endpoint için 5-10 farklı senaryo:
  - Farklı token kombinasyonları
  - Birden fazla parametre birlikte kullanımı
  - Doğal dil → parametre çıkarma kalitesi
  - Yanıt yorumlama derinliği (ham JSON yok, sayısal veri, actionable insight)
  - Türkçe + İngilizce karma sorgular
  - Belirsiz ve kesin sorgular
  - Sayfalama, filtreleme, sıralama kombinasyonları

Test sınıfları (Part A):
  TestDLMMGetPairs            — 9 test
  TestDLMMGetPair             — 8 test
  TestDLMMGetUserPositions    — 8 test
  TestDLMMGetActiveBin        — 7 test
  TestDLMMGetPoolGroups       — 8 test
  TestDLMMGetPoolGroup        — 7 test
  TestDLMMGetPoolOHLCV        — 9 test
  TestDLMMGetPoolVolumeHist   — 8 test
  TestDLMMGetProtocolStats    — 7 test
  TestDAMMv2GetPools          — 9 test
  TestDAMMv2GetPoolGroups     — 7 test
  TestDAMMv2GetPoolGroup      — 7 test
  TestDAMMv2GetPool           — 8 test
  TestDAMMv2GetPoolOHLCV      — 8 test
  TestDAMMv2GetPoolVolumeHist — 7 test
  TestDAMMv2GetProtocolMetrics— 7 test

Toplam Part A: ~124 test
"""

from __future__ import annotations

import json
import os
import re
import uuid

import httpx
import pytest

# ─── Config ───────────────────────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://localhost:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

# Gerçek DLMM havuz adresleri
DLMM_SOL_USDC  = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"
DLMM_SOL_USDT  = "ARwi1S4DaiTG5DX7S4M4ZkL2tiPGmYXLCxaXtpVXMiAm"
DLMM_BONK_SOL  = "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EkdEcsL5"
DLMM_JTO_USDC  = "7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm"
DLMM_WIF_SOL   = "CYbD9RaToYMtWKA7QZyoLahnHdWq553Vm62Lh6qWtuxq"

# DAMM v2 pool adresleri
DV2_SOL_USDC   = "FrGPG6XWqwmBpzfFBT3r6fLFXGkCsTBNYFMPqMYCasF5"
DV2_BONK_USDC  = "5xEBGSKy7bRBTEkLwRoaM1sBJBBmpAXF8N1GFrGpCWtU"

# Mint adresleri
SOL_MINT   = "So11111111111111111111111111111111111111112"
USDC_MINT  = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT  = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
BONK_MINT  = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
JTO_MINT   = "jtojtomepa8bduh8b5JsfX6LB6BH1EaD7SyC6vDKpLc"
WIF_MINT   = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP_MINT   = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
PYTH_MINT  = "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"

USER_WALLET = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"
OTHER_WALLET = "7nYabs9dUhvxYwdTnrWVBL129NNrvYxvBBVsbkr2dwQW"
FAKE_POOL    = "FakePool111111111111111111111111111111111111"


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


def _session_id() -> str:
    return f"local:{uuid.uuid4()}"


# ─── SSE Parser ───────────────────────────────────────────────────────────────

class StreamResult:
    def __init__(self):
        self.text: str = ""
        self.actions: list[dict] = []
        self.queries: list[dict] = []
        self.clarify: list[dict] = []
        self.errors: list[dict] = []
        self.done: bool = False
        self.raw_events: list[dict] = []

    @classmethod
    def parse(cls, response_text: str) -> "StreamResult":
        result = cls()
        for line in response_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                result.done = True
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            result.raw_events.append(evt)
            if "delta" in evt:
                result.text += evt["delta"]
            elif "action" in evt:
                result.actions.append(evt["action"])
            elif "query" in evt:
                result.queries.append(evt["query"])
            elif "clarify" in evt:
                result.clarify.append(evt["clarify"])
            elif "error" in evt:
                result.errors.append(evt)
        return result

    def all_triggered_types(self) -> list[str]:
        return [a.get("type", "") for a in self.actions + self.queries]

    def params_for(self, action_type: str) -> dict:
        for item in self.actions + self.queries:
            if item.get("type") == action_type:
                return item.get("params", {})
        return {}

    def asked_clarification(self) -> bool:
        return len(self.clarify) > 0

    def has_text_content(self) -> bool:
        return len(self.text.strip()) > 30

    def text_lower(self) -> str:
        return self.text.lower()


def _chat(client: httpx.Client, message: str) -> StreamResult:
    payload = {"sessionId": _session_id(), "content": message, "attachments": []}
    with client.stream("POST", "/messages/stream", json=payload, headers=_headers()) as r:
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        body = r.read().decode()
    return StreamResult.parse(body)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def assert_triggered(r: StreamResult, t: str, msg: str = ""):
    assert t in r.all_triggered_types(), (
        f"'{t}' tetiklenmedi. Tetiklenenler: {r.all_triggered_types()}\n"
        f"LLM: {r.text[:400]}\n{msg}"
    )


def assert_not_triggered(r: StreamResult, t: str, msg: str = ""):
    assert t not in r.all_triggered_types(), (
        f"Unwanted '{t}' was triggered.\nLLM: {r.text[:300]}\n{msg}"
    )


def assert_param(r: StreamResult, action: str, key: str, contains: str | None = None, msg: str = ""):
    for item in r.actions + r.queries:
        if item.get("type") == action:
            p = item.get("params", {})
            assert key in p, f"[{action}] '{key}' eksik. Params: {p}\n{msg}"
            if contains:
                assert contains.lower() in str(p[key]).lower(), (
                    f"[{action}] '{key}'={p[key]!r} does not contain '{contains}'\n{msg}"
                )
            return
    pytest.fail(f"[{action}] never triggered. Triggered: {r.all_triggered_types()}\n{msg}")


def assert_param_absent(r: StreamResult, action: str, key: str, msg: str = ""):
    p = r.params_for(action)
    assert key not in p or p[key] is None, (
        f"[{action}] '{key}' should not have been sent: {p[key]!r}\n{msg}"
    )


def assert_no_raw_json(r: StreamResult, msg: str = ""):
    t = r.text
    assert not (t.count("{") + t.count("}") > 10 and t.count('"') > 20), (
        f"LLM dumped raw JSON.\n{t[:500]}\n{msg}"
    )


def assert_has_number(r: StreamResult, msg: str = ""):
    assert re.search(r"\d[\d,.]*", r.text), (
        f"No number in the LLM response.\n{r.text[:400]}\n{msg}"
    )


def assert_mentions(r: StreamResult, keywords: list[str], min_hits: int = 1, msg: str = ""):
    lower = r.text_lower()
    hits = [kw for kw in keywords if kw.lower() in lower]
    assert len(hits) >= min_hits, (
        f"LLM contains none of: {keywords}\nText: {r.text[:400]}\n{msg}"
    )


def assert_no_hallucination(r: StreamResult, msg: str = ""):
    phrases = [
        "gerçek zamanlı veriye erişimim yok",
        "i don't have access to real-time",
        "as of my knowledge cutoff",
        "i cannot access live",
    ]
    lower = r.text_lower()
    for p in phrases:
        assert p not in lower, f"LLM halusinasyon yaptı: '{p}'\nMetin: {r.text[:400]}\n{msg}"


def assert_explains_error(r: StreamResult, msg: str = ""):
    kws = ["hata", "geçersiz", "bulunamadı", "error", "invalid", "not found", "sorry", "üzgün"]
    lower = r.text_lower()
    assert any(k in lower for k in kws) or r.asked_clarification(), (
        f"No explanation for the error.\nText: {r.text[:400]}\n{msg}"
    )


def assert_page_params_present(r: StreamResult, action: str, msg: str = ""):
    p = r.params_for(action)
    has_page = "page" in p or "offset" in p
    has_size = "size" in p or "limit" in p or "pageSize" in p
    assert has_page or has_size, (
        f"[{action}] sayfalama parametresi gönderilmedi. Params: {p}\n{msg}"
    )


def assert_time_range_present(r: StreamResult, action: str, msg: str = ""):
    p = r.params_for(action)
    keys = set(p.keys())
    time_keys = {"startTime", "endTime", "from", "to", "start", "end", "startTimestamp", "endTimestamp"}
    found = keys & time_keys
    assert found, f"[{action}] zaman aralığı parametresi yok. Params: {p}\n{msg}"


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_pairs
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetPairs:
    """DLMM get_pairs: 9 farklı senaryo"""

    def test_basic_list_all_pairs(self, chat_client):
        r = _chat(chat_client, "Show me all DLMM liquidity pairs on Meteora")
        assert_triggered(r, "DLMM_GET_PAIRS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_filter_by_token_name_sol(self, chat_client):
        r = _chat(chat_client, "DLMM'de SOL içeren tüm havuzları listele")
        assert_triggered(r, "DLMM_GET_PAIRS")
        p = r.params_for("DLMM_GET_PAIRS")
        search_val = str(p.get("q", "") or p.get("search", "") or p.get("searchTerms", "")).lower()
        assert "sol" in search_val or "sol" in str(p).lower(), (
            f"'SOL' filtresi parametreye yansımadı. Params: {p}"
        )

    def test_filter_by_mint_address(self, chat_client):
        r = _chat(chat_client, f"List DLMM pairs that include the token {BONK_MINT}")
        assert_triggered(r, "DLMM_GET_PAIRS")
        p = r.params_for("DLMM_GET_PAIRS")
        assert BONK_MINT in str(p), f"BONK mint adresi parametreye girmedi. Params: {p}"

    def test_pagination_page_2(self, chat_client):
        r = _chat(chat_client, "Get DLMM pairs page 2, 20 results per page")
        assert_triggered(r, "DLMM_GET_PAIRS")
        p = r.params_for("DLMM_GET_PAIRS")
        page_val = int(p.get("page", p.get("offset", 0)))
        size_val = int(p.get("pageSize", p.get("limit", p.get("size", 0))))
        assert page_val >= 2 or size_val == 20, (
            f"Sayfalama parametreleri yanlış. page={page_val}, size={size_val}\nParams: {p}"
        )

    def test_high_tvl_filter_semantic(self, chat_client):
        r = _chat(chat_client, "Show Meteora DLMM pools with the most liquidity, top 10")
        assert_triggered(r, "DLMM_GET_PAIRS")
        assert_no_raw_json(r)
        assert_mentions(r, ["tvl", "liquidity", "likidite", "pool", "pair"], min_hits=1)

    def test_wif_usdc_specific_pair(self, chat_client):
        r = _chat(chat_client, "WIF/USDC DLMM havuzlarını göster")
        assert_triggered(r, "DLMM_GET_PAIRS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_response_quality_mentions_key_metrics(self, chat_client):
        r = _chat(chat_client, "DLMM SOL-USDC havuzlarının APR ve TVL bilgisini getir")
        assert_triggered(r, "DLMM_GET_PAIRS")
        assert_no_raw_json(r)
        assert_has_number(r, "APR/TVL sayısı gösterilmeli")

    def test_jup_sol_pair(self, chat_client):
        r = _chat(chat_client, f"Find DLMM pools for JUP token, mint: {JUP_MINT}")
        assert_triggered(r, "DLMM_GET_PAIRS")
        p = r.params_for("DLMM_GET_PAIRS")
        assert JUP_MINT in str(p) or "jup" in str(p).lower(), (
            f"JUP adresi veya sembolü yok. Params: {p}"
        )

    def test_combined_search_and_sort(self, chat_client):
        r = _chat(chat_client, "Show top DLMM BONK pools sorted by volume, limit 5")
        assert_triggered(r, "DLMM_GET_PAIRS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_pair (single pool detail)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetPair:
    """DLMM get_pair: 8 senaryo"""

    def test_detail_by_known_address(self, chat_client):
        r = _chat(chat_client, f"Give me details for DLMM pool {DLMM_SOL_USDC}")
        assert_triggered(r, "DLMM_GET_PAIR")
        assert_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_SOL_USDC)

    def test_natural_language_sol_usdc(self, chat_client):
        r = _chat(chat_client, "What's the current bin step and fee for the SOL-USDC DLMM pool?")
        assert_triggered(r, "DLMM_GET_PAIR")
        assert_no_raw_json(r)
        assert_mentions(r, ["bin", "fee", "ücret", "step", "adım", "sol", "usdc"], min_hits=1)

    def test_response_includes_bin_range(self, chat_client):
        r = _chat(chat_client, f"Show bin range and active price for DLMM pool {DLMM_BONK_SOL}")
        assert_triggered(r, "DLMM_GET_PAIR")
        assert_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_BONK_SOL)
        assert_no_raw_json(r)

    def test_fee_tier_query(self, chat_client):
        r = _chat(chat_client, f"Ne kadar fee alıyor bu DLMM havuzu: {DLMM_JTO_USDC}?")
        assert_triggered(r, "DLMM_GET_PAIR")
        assert_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_JTO_USDC)
        assert_mentions(r, ["fee", "ücret", "komisyon", "%", "basis"], min_hits=1)

    def test_invalid_address_handled(self, chat_client):
        r = _chat(chat_client, f"DLMM pool detaylarını getir: {FAKE_POOL}")
        assert_no_raw_json(r)
        # Either triggers and fails gracefully, or LLM explains the issue
        if "DLMM_GET_PAIR" in r.all_triggered_types():
            assert_param(r, "DLMM_GET_PAIR", "poolAddress")
        else:
            assert_explains_error(r, "Geçersiz adres için açıklama bekleniyor")

    def test_wif_sol_pool_detail(self, chat_client):
        r = _chat(chat_client, f"What is the liquidity distribution for DLMM pool {DLMM_WIF_SOL}?")
        assert_triggered(r, "DLMM_GET_PAIR")
        assert_no_raw_json(r)

    def test_response_quality_no_raw_json_with_explanation(self, chat_client):
        r = _chat(chat_client, f"Analyze this DLMM pool for me: {DLMM_SOL_USDT}")
        assert_triggered(r, "DLMM_GET_PAIR")
        assert_no_raw_json(r)
        assert_has_number(r, "Analizde sayısal veri bekleniyor")

    def test_address_in_text_extracted(self, chat_client):
        r = _chat(chat_client, (
            f"I want to add liquidity to this pool. Can you check if it's active? "
            f"Pool: {DLMM_SOL_USDC}"
        ))
        assert_triggered(r, "DLMM_GET_PAIR")
        assert_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_SOL_USDC)


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_user_positions
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetUserPositions:
    """DLMM get_user_positions: 8 senaryo"""

    def test_my_positions_default_wallet(self, chat_client):
        r = _chat(chat_client, "Show my DLMM positions on Meteora")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        p = r.params_for("DLMM_GET_USER_POSITIONS")
        assert "walletAddress" in p or "wallet" in str(p).lower(), (
            f"Wallet adresi parametreye girmedi. Params: {p}"
        )

    def test_explicit_wallet_address(self, chat_client):
        r = _chat(chat_client, f"What DLMM positions does wallet {OTHER_WALLET} have?")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        assert_param(r, "DLMM_GET_USER_POSITIONS", "walletAddress", contains=OTHER_WALLET)

    def test_positions_with_pnl(self, chat_client):
        r = _chat(chat_client, "DLMM'deki pozisyonlarımın PnL'ini göster")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_positions_in_specific_pool(self, chat_client):
        r = _chat(chat_client, f"Do I have any positions in DLMM pool {DLMM_SOL_USDC}?")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        assert_no_raw_json(r)

    def test_response_shows_position_summary(self, chat_client):
        r = _chat(chat_client, "Meteora DLMM'deki tüm likidite pozisyonlarımı özetle")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        assert_no_raw_json(r)
        assert r.has_text_content(), "Pozisyon özeti bekleniyor"

    def test_unclaimed_fees_query(self, chat_client):
        r = _chat(chat_client, "How much unclaimed fees do I have in my DLMM positions?")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        assert_no_raw_json(r)
        assert_mentions(r, ["fee", "ücret", "unclaimed", "talep edilmemiş", "kazanç"], min_hits=1)

    def test_in_range_vs_out_of_range(self, chat_client):
        r = _chat(chat_client, "Kaç tane DLMM pozisyonum aktif aralıkta, kaçı aralık dışında?")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        assert_no_raw_json(r)

    def test_english_turkish_mixed(self, chat_client):
        r = _chat(chat_client, f"Check DLMM positions for this wallet: {USER_WALLET}, bana özet ver")
        assert_triggered(r, "DLMM_GET_USER_POSITIONS")
        assert_param(r, "DLMM_GET_USER_POSITIONS", "walletAddress", contains=USER_WALLET)


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_active_bin
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetActiveBin:
    """DLMM get_active_bin: 7 senaryo"""

    def test_active_bin_by_pool_address(self, chat_client):
        r = _chat(chat_client, f"What is the active bin for DLMM pool {DLMM_SOL_USDC}?")
        assert_triggered(r, "DLMM_GET_ACTIVE_BIN")
        assert_param(r, "DLMM_GET_ACTIVE_BIN", "poolAddress", contains=DLMM_SOL_USDC)

    def test_current_price_in_active_bin(self, chat_client):
        r = _chat(chat_client, f"SOL'un şu anki fiyatı DLMM havuzunda ne? Havuz: {DLMM_SOL_USDT}")
        assert_triggered(r, "DLMM_GET_ACTIVE_BIN")
        assert_param(r, "DLMM_GET_ACTIVE_BIN", "poolAddress", contains=DLMM_SOL_USDT)
        assert_no_raw_json(r)
        assert_has_number(r, "Aktif bin fiyatı gösterilmeli")

    def test_response_mentions_bin_id(self, chat_client):
        r = _chat(chat_client, f"Get active bin ID for {DLMM_BONK_SOL}")
        assert_triggered(r, "DLMM_GET_ACTIVE_BIN")
        assert_no_raw_json(r)

    def test_liquidity_in_active_bin(self, chat_client):
        r = _chat(chat_client, f"How much liquidity is in the active bin of {DLMM_JTO_USDC}?")
        assert_triggered(r, "DLMM_GET_ACTIVE_BIN")
        assert_param(r, "DLMM_GET_ACTIVE_BIN", "poolAddress", contains=DLMM_JTO_USDC)

    def test_wif_pool_active_price(self, chat_client):
        r = _chat(chat_client, f"WIF/SOL DLMM havuzunun aktif bin'indeki fiyatı öğrenmek istiyorum. {DLMM_WIF_SOL}")
        assert_triggered(r, "DLMM_GET_ACTIVE_BIN")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_actionable_response_for_lp(self, chat_client):
        r = _chat(chat_client, (
            f"I'm planning to add liquidity to {DLMM_SOL_USDC}. "
            "What's the current active price range I should set?"
        ))
        assert_triggered(r, "DLMM_GET_ACTIVE_BIN")
        assert_no_raw_json(r)

    def test_missing_address_asks_clarification(self, chat_client):
        r = _chat(chat_client, "What is the active bin for my favorite DLMM pool?")
        assert r.asked_clarification() or "DLMM_GET_ACTIVE_BIN" in r.all_triggered_types() or r.has_text_content(), (
            "LLM eksik adres için tepki vermedi"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_pool_groups
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetPoolGroups:
    """DLMM get_pool_groups: 8 senaryo"""

    def test_list_all_groups(self, chat_client):
        r = _chat(chat_client, "List all DLMM pool groups on Meteora")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        assert_no_raw_json(r)

    def test_filter_by_token_a(self, chat_client):
        r = _chat(chat_client, "DLMM havuz gruplarını SOL tokenı içerenler için filtrele")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        assert_no_raw_json(r)

    def test_pagination_groups(self, chat_client):
        r = _chat(chat_client, "Get DLMM pool groups, page 3, size 15")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        p = r.params_for("DLMM_GET_POOL_GROUPS")
        assert int(p.get("page", 0)) >= 3 or int(p.get("size", p.get("limit", 0))) == 15, (
            f"Pagination is wrong. Params: {p}"
        )

    def test_response_structure_quality(self, chat_client):
        r = _chat(chat_client, "Meteora'daki DLMM pool gruplarını ve her birinin TVL'ini göster")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_search_usdc_groups(self, chat_client):
        r = _chat(chat_client, "Find DLMM pool groups containing USDC")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        assert_no_hallucination(r)

    def test_high_volume_groups(self, chat_client):
        r = _chat(chat_client, "En yüksek hacimli DLMM havuz gruplarını listele")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        assert_no_raw_json(r)

    def test_bonk_groups_search(self, chat_client):
        r = _chat(chat_client, "DLMM BONK pool groups nelerdir?")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        assert_no_raw_json(r)

    def test_combined_filter_sort(self, chat_client):
        r = _chat(chat_client, "Show DLMM pool groups with SOL, sorted by TVL descending, first 10")
        assert_triggered(r, "DLMM_GET_POOL_GROUPS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_pool_group (single group)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetPoolGroup:
    """DLMM get_pool_group: 7 senaryo"""

    def test_group_detail_by_name(self, chat_client):
        r = _chat(chat_client, "Show details for the SOL-USDC DLMM pool group")
        assert_triggered(r, "DLMM_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_group_tvl_breakdown(self, chat_client):
        r = _chat(chat_client, "SOL-USDC DLMM grubunun toplam TVL dağılımını göster")
        assert_triggered(r, "DLMM_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_group_fee_comparison(self, chat_client):
        r = _chat(chat_client, "What fee tiers are available in the BONK-SOL DLMM group?")
        assert_triggered(r, "DLMM_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert_mentions(r, ["fee", "tier", "ücret", "%", "basis point", "bps"], min_hits=1)

    def test_group_by_token_pair(self, chat_client):
        r = _chat(chat_client, "JTO-USDC DLMM pool grubunu analiz et")
        assert_triggered(r, "DLMM_GET_POOL_GROUP")
        assert_no_raw_json(r)

    def test_group_recommendations(self, chat_client):
        r = _chat(chat_client, (
            "I want to provide liquidity to WIF-SOL. "
            "Which fee tier in the DLMM group would be best?"
        ))
        assert_triggered(r, "DLMM_GET_POOL_GROUP")
        assert_no_raw_json(r)

    def test_group_apr_info(self, chat_client):
        r = _chat(chat_client, "SOL-USDT DLMM grubunun ortalama APR'ı nedir?")
        assert_triggered(r, "DLMM_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_group_all_pools_in_it(self, chat_client):
        r = _chat(chat_client, "How many pools are in the SOL-USDC DLMM group? List them.")
        assert_triggered(r, "DLMM_GET_POOL_GROUP")
        assert_no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_pool_ohlcv
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetPoolOHLCV:
    """DLMM get_pool_ohlcv: 9 senaryo"""

    def test_ohlcv_daily_by_pool(self, chat_client):
        r = _chat(chat_client, f"Show daily OHLCV data for DLMM pool {DLMM_SOL_USDC}")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        assert_param(r, "DLMM_GET_POOL_OHLCV", "poolAddress", contains=DLMM_SOL_USDC)

    def test_ohlcv_hourly_resolution(self, chat_client):
        r = _chat(chat_client, f"Get 1-hour candles for pool {DLMM_BONK_SOL}")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        p = r.params_for("DLMM_GET_POOL_OHLCV")
        resolution = str(p.get("resolution", p.get("timeframe", p.get("interval", "")))).lower()
        assert "hour" in resolution or "1h" in resolution or "3600" in resolution or resolution == "", (
            f"1 saatlik resolüsyon yanlış: {resolution}"
        )

    def test_ohlcv_last_7_days(self, chat_client):
        r = _chat(chat_client, f"DLMM havuzu {DLMM_SOL_USDT} için son 7 günün fiyat verilerini getir")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        assert_param(r, "DLMM_GET_POOL_OHLCV", "poolAddress", contains=DLMM_SOL_USDT)
        assert_no_raw_json(r)

    def test_ohlcv_weekly_resolution(self, chat_client):
        r = _chat(chat_client, f"Show weekly price chart data for {DLMM_WIF_SOL}")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        assert_no_raw_json(r)

    def test_ohlcv_response_includes_price(self, chat_client):
        r = _chat(chat_client, f"What was SOL's high and low price last week in pool {DLMM_SOL_USDC}?")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_mentions(r, ["high", "low", "en yüksek", "en düşük", "price", "fiyat"], min_hits=1)

    def test_ohlcv_with_from_to_dates(self, chat_client):
        r = _chat(chat_client, (
            f"Get DLMM pool {DLMM_JTO_USDC} price history "
            "from January 1 2025 to January 31 2025"
        ))
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        assert_no_raw_json(r)

    def test_ohlcv_5min_candles(self, chat_client):
        r = _chat(chat_client, f"5 dakikalık mumlar {DLMM_BONK_SOL} için göster")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        p = r.params_for("DLMM_GET_POOL_OHLCV")
        resolution = str(p.get("resolution", p.get("timeframe", ""))).lower()
        assert "5m" in resolution or "300" in resolution or "min" in resolution or resolution == "", (
            f"5 dakika resolüsyonu yanlış: {resolution}"
        )

    def test_ohlcv_volume_included(self, chat_client):
        r = _chat(chat_client, f"Show OHLCV with volume for {DLMM_SOL_USDC} past 30 days")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        assert_no_raw_json(r)

    def test_ohlcv_response_no_hallucination(self, chat_client):
        r = _chat(chat_client, f"Bu DLMM havuzunun geçen ay açılış fiyatı neydi? {DLMM_SOL_USDT}")
        assert_triggered(r, "DLMM_GET_POOL_OHLCV")
        assert_no_hallucination(r)
        assert_no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_pool_volume_history
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetPoolVolumeHistory:
    """DLMM get_pool_volume_history: 8 senaryo"""

    def test_volume_history_daily(self, chat_client):
        r = _chat(chat_client, f"Show daily volume history for DLMM pool {DLMM_SOL_USDC}")
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_param(r, "DLMM_GET_POOL_VOLUME_HISTORY", "poolAddress", contains=DLMM_SOL_USDC)

    def test_volume_last_30_days(self, chat_client):
        r = _chat(chat_client, f"Son 30 günün hacim geçmişini göster: {DLMM_BONK_SOL}")
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_volume_trend_analysis(self, chat_client):
        r = _chat(chat_client, (
            f"Is the trading volume in pool {DLMM_SOL_USDT} increasing or decreasing? "
            "Show me the trend."
        ))
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_volume_peak_day(self, chat_client):
        r = _chat(chat_client, f"Which day had the highest volume in {DLMM_WIF_SOL}?")
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)

    def test_volume_weekly_resolution(self, chat_client):
        r = _chat(chat_client, f"Show weekly volume breakdown for {DLMM_JTO_USDC}")
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)

    def test_fee_revenue_from_volume(self, chat_client):
        r = _chat(chat_client, (
            f"How much fee revenue did pool {DLMM_SOL_USDC} generate last week "
            "based on volume?"
        ))
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)

    def test_volume_comparison_response(self, chat_client):
        r = _chat(chat_client, f"Bu hafta ile geçen haftanın hacmini karşılaştır: {DLMM_SOL_USDC}")
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)

    def test_volume_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            f"Should I add liquidity to {DLMM_BONK_SOL}? "
            "Show me the volume trend first."
        ))
        assert_triggered(r, "DLMM_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_protocol_stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetProtocolStats:
    """DLMM get_protocol_stats: 7 senaryo"""

    def test_overall_protocol_stats(self, chat_client):
        r = _chat(chat_client, "What are the overall DLMM protocol statistics?")
        assert_triggered(r, "DLMM_GET_PROTOCOL_STATS")
        assert_no_raw_json(r)

    def test_total_tvl_question(self, chat_client):
        r = _chat(chat_client, "Meteora DLMM'nin toplam TVL'si nedir?")
        assert_triggered(r, "DLMM_GET_PROTOCOL_STATS")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_no_hallucination(r)

    def test_daily_volume_protocol(self, chat_client):
        r = _chat(chat_client, "How much daily trading volume does Meteora DLMM process?")
        assert_triggered(r, "DLMM_GET_PROTOCOL_STATS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_total_fee_revenue(self, chat_client):
        r = _chat(chat_client, "Meteora DLMM'nin bugüne kadar oluşturduğu toplam ücret geliri nedir?")
        assert_triggered(r, "DLMM_GET_PROTOCOL_STATS")
        assert_no_raw_json(r)

    def test_protocol_comparison_question(self, chat_client):
        r = _chat(chat_client, "How does Meteora DLMM compare in terms of total liquidity?")
        assert_triggered(r, "DLMM_GET_PROTOCOL_STATS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_no_params_required(self, chat_client):
        r = _chat(chat_client, "Get Meteora DLMM protocol metrics")
        assert_triggered(r, "DLMM_GET_PROTOCOL_STATS")
        p = r.params_for("DLMM_GET_PROTOCOL_STATS")
        assert len(p) == 0 or all(v is None for v in p.values()), (
            f"This endpoint takes no params but got: {p}"
        )

    def test_growth_trend_response(self, chat_client):
        r = _chat(chat_client, "Is Meteora DLMM growing? Show me protocol-level stats.")
        assert_triggered(r, "DLMM_GET_PROTOCOL_STATS")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v2 — get_pools
# ═══════════════════════════════════════════════════════════════════════════════

class TestDAMMv2GetPools:
    """DAMM v2 get_pools: 9 senaryo"""

    def test_list_all_v2_pools(self, chat_client):
        r = _chat(chat_client, "List all Meteora DAMM v2 liquidity pools")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_filter_sol_usdc_v2(self, chat_client):
        r = _chat(chat_client, "Show DAMM v2 pools for SOL-USDC pair")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        assert_no_raw_json(r)

    def test_filter_by_token_mint(self, chat_client):
        r = _chat(chat_client, f"Find DAMM v2 pools with token {BONK_MINT}")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        p = r.params_for("DAMMV2_GET_POOLS")
        assert BONK_MINT in str(p), f"BONK mint gönderilmedi. Params: {p}"

    def test_pagination_v2_pools(self, chat_client):
        r = _chat(chat_client, "Get DAMM v2 pools, page 2, 25 per page")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        p = r.params_for("DAMMV2_GET_POOLS")
        page = int(p.get("page", 0))
        size = int(p.get("pageSize", p.get("size", p.get("limit", 0))))
        assert page >= 2 or size == 25, f"Pagination is wrong. Params: {p}"

    def test_high_tvl_v2_pools(self, chat_client):
        r = _chat(chat_client, "DAMM v2'de en yüksek TVL'ye sahip havuzları listele")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_jup_token_v2_pools(self, chat_client):
        r = _chat(chat_client, "Are there any JUP pools on DAMM v2?")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_v2_vs_v1_routing(self, chat_client):
        r = _chat(chat_client, "Show me DAMM v2 pools (not v1, not DLMM)")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        assert_not_triggered(r, "DLMM_GET_PAIRS")
        assert_not_triggered(r, "DAMMV1_GET_POOLS")

    def test_search_wif_v2(self, chat_client):
        r = _chat(chat_client, "WIF token için DAMM v2 havuzları var mı?")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        assert_no_raw_json(r)

    def test_response_key_metrics(self, chat_client):
        r = _chat(chat_client, "List DAMM v2 pools with their APR and fee rate")
        assert_triggered(r, "DAMMV2_GET_POOLS")
        assert_no_raw_json(r)
        assert_has_number(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v2 — get_pool_groups
# ═══════════════════════════════════════════════════════════════════════════════

class TestDAMMv2GetPoolGroups:
    """DAMM v2 get_pool_groups: 7 senaryo"""

    def test_list_v2_groups(self, chat_client):
        r = _chat(chat_client, "List all DAMM v2 pool groups on Meteora")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUPS")
        assert_no_raw_json(r)

    def test_sol_usdc_group_v2(self, chat_client):
        r = _chat(chat_client, "DAMM v2'deki SOL-USDC grup bilgilerini göster")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUPS")
        assert_no_raw_json(r)

    def test_pagination_groups_v2(self, chat_client):
        r = _chat(chat_client, "DAMM v2 pool groups, page 2, 20 groups per page")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUPS")
        p = r.params_for("DAMMV2_GET_POOL_GROUPS")
        assert int(p.get("page", 0)) >= 2 or int(p.get("size", p.get("limit", 0))) == 20, (
            f"Pagination is wrong. Params: {p}"
        )

    def test_group_tvl_sorted(self, chat_client):
        r = _chat(chat_client, "En büyük DAMM v2 havuz gruplarını TVL'ye göre göster")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUPS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_bonk_groups_v2(self, chat_client):
        r = _chat(chat_client, "DAMM v2 BONK pool groups list")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUPS")
        assert_no_raw_json(r)

    def test_no_hallucination_groups(self, chat_client):
        r = _chat(chat_client, "What pool groups are available in Meteora DAMM v2?")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUPS")
        assert_no_hallucination(r)

    def test_response_quality(self, chat_client):
        r = _chat(chat_client, "DAMM v2 pool gruplarını özetle, en aktif olanları belirt")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUPS")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v2 — get_pool_group (single)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDAMMv2GetPoolGroup:
    """DAMM v2 get_pool_group: 7 senaryo"""

    def test_group_by_name_sol_usdc(self, chat_client):
        r = _chat(chat_client, "Show the SOL-USDC DAMM v2 pool group")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_group_fee_tiers(self, chat_client):
        r = _chat(chat_client, "DAMM v2 SOL-USDT grubundaki fee tier seçenekleri neler?")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert_mentions(r, ["fee", "tier", "ücret", "%", "bps"], min_hits=1)

    def test_group_which_pool_to_pick(self, chat_client):
        r = _chat(chat_client, (
            "I want to LP in DAMM v2 BONK-USDC. "
            "Which pool in the group has the best APR?"
        ))
        assert_triggered(r, "DAMMV2_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_group_total_liquidity(self, chat_client):
        r = _chat(chat_client, "DAMM v2 WIF-SOL grubunun toplam likidite miktarı nedir?")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUP")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_group_all_pools_listed(self, chat_client):
        r = _chat(chat_client, "List all pools in the JTO-USDC DAMM v2 group")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUP")
        assert_no_raw_json(r)

    def test_group_volume_comparison(self, chat_client):
        r = _chat(chat_client, "Compare the pools in DAMM v2 SOL-USDC group by volume")
        assert_triggered(r, "DAMMV2_GET_POOL_GROUP")
        assert_no_raw_json(r)

    def test_group_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            "I'm comparing DAMM v2 vs DLMM for SOL-USDC. "
            "Show me the DAMM v2 group first."
        ))
        assert_triggered(r, "DAMMV2_GET_POOL_GROUP")
        assert_no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v2 — get_pool (single pool detail)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDAMMv2GetPool:
    """DAMM v2 get_pool: 8 senaryo"""

    def test_pool_detail_by_address(self, chat_client):
        r = _chat(chat_client, f"Get details for DAMM v2 pool {DV2_SOL_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL")
        assert_param(r, "DAMMV2_GET_POOL", "poolAddress", contains=DV2_SOL_USDC)

    def test_pool_fee_rate(self, chat_client):
        r = _chat(chat_client, f"DAMM v2 havuzunun fee oranı nedir? {DV2_BONK_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL")
        assert_no_raw_json(r)
        assert_mentions(r, ["fee", "ücret", "%", "bps", "komisyon"], min_hits=1)

    def test_pool_current_price(self, chat_client):
        r = _chat(chat_client, f"What is the current price in DAMM v2 pool {DV2_SOL_USDC}?")
        assert_triggered(r, "DAMMV2_GET_POOL")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_pool_tvl_query(self, chat_client):
        r = _chat(chat_client, f"Bu DAMM v2 havuzunun TVL'si ne kadar? {DV2_SOL_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_pool_lp_token_info(self, chat_client):
        r = _chat(chat_client, f"Tell me about the LP token for DAMM v2 pool {DV2_BONK_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL")
        assert_no_raw_json(r)

    def test_pool_reserves(self, chat_client):
        r = _chat(chat_client, f"How much SOL and USDC are in the reserves of pool {DV2_SOL_USDC}?")
        assert_triggered(r, "DAMMV2_GET_POOL")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_pool_invalid_address(self, chat_client):
        r = _chat(chat_client, f"DAMM v2 pool: {FAKE_POOL}")
        if "DAMMV2_GET_POOL" in r.all_triggered_types():
            assert_no_raw_json(r)
        else:
            assert_explains_error(r)

    def test_pool_response_for_lp_decision(self, chat_client):
        r = _chat(chat_client, (
            f"I'm considering adding liquidity to DAMM v2 pool {DV2_SOL_USDC}. "
            "What metrics should I know?"
        ))
        assert_triggered(r, "DAMMV2_GET_POOL")
        assert_no_raw_json(r)
        assert r.has_text_content()
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v2 — get_pool_ohlcv
# ═══════════════════════════════════════════════════════════════════════════════

class TestDAMMv2GetPoolOHLCV:
    """DAMM v2 get_pool_ohlcv: 8 senaryo"""

    def test_ohlcv_daily(self, chat_client):
        r = _chat(chat_client, f"Get daily OHLCV data for DAMM v2 pool {DV2_SOL_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_param(r, "DAMMV2_GET_POOL_OHLCV", "poolAddress", contains=DV2_SOL_USDC)

    def test_ohlcv_15min(self, chat_client):
        r = _chat(chat_client, f"15 dakikalık mum verileri {DV2_BONK_USDC} için")
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_no_raw_json(r)

    def test_ohlcv_last_month(self, chat_client):
        r = _chat(chat_client, f"DAMM v2 {DV2_SOL_USDC} havuzunun geçen ay fiyat grafiğini göster")
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_ohlcv_close_price(self, chat_client):
        r = _chat(chat_client, f"What was the closing price yesterday in pool {DV2_SOL_USDC}?")
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_ohlcv_volume_column(self, chat_client):
        r = _chat(chat_client, f"Show OHLCV including volume for DAMM v2 {DV2_BONK_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_no_raw_json(r)

    def test_ohlcv_hourly_3days(self, chat_client):
        r = _chat(chat_client, f"DAMM v2 pool {DV2_SOL_USDC} için saatlik 3 günlük OHLCV")
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_no_raw_json(r)

    def test_ohlcv_response_interprets_trend(self, chat_client):
        r = _chat(chat_client, (
            f"Is the price trending up or down in DAMM v2 pool {DV2_SOL_USDC}? "
            "Show weekly candles."
        ))
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_ohlcv_turkish_query(self, chat_client):
        r = _chat(chat_client, f"DAMM v2 havuzunun dünkü yüksek ve düşük değerleri: {DV2_SOL_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL_OHLCV")
        assert_no_raw_json(r)
        assert_has_number(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v2 — get_pool_volume_history
# ═══════════════════════════════════════════════════════════════════════════════

class TestDAMMv2GetPoolVolumeHistory:
    """DAMM v2 get_pool_volume_history: 7 senaryo"""

    def test_volume_history_basic(self, chat_client):
        r = _chat(chat_client, f"Show volume history for DAMM v2 pool {DV2_SOL_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL_VOLUME_HISTORY")
        assert_param(r, "DAMMV2_GET_POOL_VOLUME_HISTORY", "poolAddress", contains=DV2_SOL_USDC)

    def test_volume_last_week(self, chat_client):
        r = _chat(chat_client, f"DAMM v2 {DV2_BONK_USDC} geçen haftanın günlük hacim geçmişi")
        assert_triggered(r, "DAMMV2_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_volume_peak_analysis(self, chat_client):
        r = _chat(chat_client, f"When was the peak trading day for {DV2_SOL_USDC}?")
        assert_triggered(r, "DAMMV2_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_volume_monthly_view(self, chat_client):
        r = _chat(chat_client, f"Show monthly volume for DAMM v2 {DV2_SOL_USDC}")
        assert_triggered(r, "DAMMV2_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)

    def test_fee_revenue_estimate(self, chat_client):
        r = _chat(chat_client, (
            f"Based on volume, how much fee income is {DV2_SOL_USDC} generating per day?"
        ))
        assert_triggered(r, "DAMMV2_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)

    def test_volume_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            f"Hacim trendi artan mı azalan mı? {DV2_BONK_USDC} için analiz yap"
        ))
        assert_triggered(r, "DAMMV2_GET_POOL_VOLUME_HISTORY")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_volume_no_hallucination(self, chat_client):
        r = _chat(chat_client, f"What was the volume in pool {DV2_SOL_USDC} last Monday?")
        assert_triggered(r, "DAMMV2_GET_POOL_VOLUME_HISTORY")
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v2 — get_protocol_metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestDAMMv2GetProtocolMetrics:
    """DAMM v2 get_protocol_metrics: 7 senaryo"""

    def test_overall_metrics(self, chat_client):
        r = _chat(chat_client, "What are the Meteora DAMM v2 protocol metrics?")
        assert_triggered(r, "DAMMV2_GET_PROTOCOL_METRICS")
        assert_no_raw_json(r)

    def test_total_tvl_v2(self, chat_client):
        r = _chat(chat_client, "DAMM v2'nin toplam TVL'si nedir?")
        assert_triggered(r, "DAMMV2_GET_PROTOCOL_METRICS")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_no_hallucination(r)

    def test_daily_volume_v2_protocol(self, chat_client):
        r = _chat(chat_client, "How much volume does Meteora DAMM v2 process daily?")
        assert_triggered(r, "DAMMV2_GET_PROTOCOL_METRICS")
        assert_no_raw_json(r)

    def test_no_params_for_metrics(self, chat_client):
        r = _chat(chat_client, "Get DAMM v2 protocol-level statistics")
        assert_triggered(r, "DAMMV2_GET_PROTOCOL_METRICS")
        p = r.params_for("DAMMV2_GET_PROTOCOL_METRICS")
        assert len(p) == 0 or all(v is None for v in p.values()), (
            f"This endpoint takes no params but got: {p}"
        )

    def test_pool_count_response(self, chat_client):
        r = _chat(chat_client, "Meteora DAMM v2'de kaç tane aktif havuz var?")
        assert_triggered(r, "DAMMV2_GET_PROTOCOL_METRICS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_growth_comparison(self, chat_client):
        r = _chat(chat_client, "Is DAMM v2 growing in terms of total value locked?")
        assert_triggered(r, "DAMMV2_GET_PROTOCOL_METRICS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_fee_cumulative_v2(self, chat_client):
        r = _chat(chat_client, "DAMM v2 protokolünün bugüne kadar topladığı kümülatif fee miktarı")
        assert_triggered(r, "DAMMV2_GET_PROTOCOL_METRICS")
        assert_no_raw_json(r)

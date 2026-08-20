"""
Meteora DAMM v2 — comprehensive LLM quality suite

These tests check more than action routing. They check whether the LLM:
  - interprets the data instead of restating it
  - reports numeric values correctly
  - reports errors to the user gracefully
  - routes ambiguous questions to the right place
  - extracts every parameter, and extracts them correctly
  - produces prose rather than dumping raw JSON

The user-facing prompts are deliberately written in Turkish: this suite doubles
as the regression net for Turkish-language intent handling.

Test classes:
  TestDV2GetPools             — 18 tests (listing, sorting, filtering, interpretation)
  TestDV2GetPoolGroups        — 16 tests (token-pair groups, parameters, interpretation)
  TestDV2GetPoolGroup         — 12 tests (a specific pair, mint parsing, errors)
  TestDV2GetPool              — 14 tests (single pool, TVL/price/APR reading, errors)
  TestDV2GetPoolOhlcv         — 18 tests (timeframe, timestamp ban, interpretation)
  TestDV2GetPoolVolHistory    — 16 tests (volume trend, timeframe, interpretation)
  TestDV2GetProtocolMetrics   — 12 tests (global stats, interpretation, no-arg call)
  TestDV2Routing              — 16 tests (DLMM vs v2, v1 vs v2, ambiguous)
  TestDV2ErrorHandling        — 14 tests (invalid address, missing param, service error)
  TestDV2UserGuidance         — 14 tests (clarification, routing, warnings)
  TestDV2InterpretationDepth  — 14 tests (analysis, comparison, trend reading)

Total: ~164 tests
"""

from __future__ import annotations

import json
import os
import re
import uuid

import httpx
import pytest

# ─── Service configuration ─────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://localhost:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

# Known DAMM v2 pool addresses (real, mainnet)
DV2_SOL_USDC  = "9TtTGp8JeYmLBsMCJfQCdMScGsEMmhqPWGDykELMPMv6"
DV2_SOL_USDT  = "FoSDw2L5DmTuQTFe55gTtzxXdUznxRoaSET5sD3W6wmn"
DV2_BONK_SOL  = "BmReo2dJHGHEFNfwM3WGzBmGHScqBPYg39r3VGexdNFH"

SOL_MINT   = "So11111111111111111111111111111111111111112"
USDC_MINT  = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT  = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
BONK_MINT  = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# Deliberately invalid addresses
FAKE_ADDR    = "FakeAddr111111111111111111111111111111111111"
GARBAGE_ADDR = "not-a-solana-address"
SHORT_ADDR   = "abc"

VALID_TIMEFRAMES = {"5m", "30m", "1h", "2h", "4h", "12h", "24h"}


# ─── Fixtures ──────────────────────────────────────────────────────────────

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


# ─── SSE stream parser ────────────────────────────────────────────────────

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


# ─── Assertion helpers ────────────────────────────────────────────────────

def assert_action_triggered(result: StreamResult, expected: str, msg: str = ""):
    triggered = result.all_triggered_types()
    assert expected in triggered, (
        f"'{expected}' tetiklenmedi.\n"
        f"Tetiklenenler: {triggered}\n"
        f"LLM metni: {result.text[:500]}\n"
        f"Mesaj: {msg}"
    )


def assert_not_triggered(result: StreamResult, unwanted: str, msg: str = ""):
    triggered = result.all_triggered_types()
    assert unwanted not in triggered, (
        f"Unwanted '{unwanted}' was triggered.\n"
        f"Tetiklenenler: {triggered}\n"
        f"LLM metni: {result.text[:300]}\n{msg}"
    )


def assert_param_present(
    result: StreamResult, action: str, key: str,
    contains: str | None = None, msg: str = ""
):
    for item in result.actions + result.queries:
        if item.get("type") == action:
            p = item.get("params", {})
            assert key in p, (
                f"[{action}] '{key}' parametresi eksik.\nParams: {p}\n{msg}"
            )
            if contains:
                assert contains.lower() in str(p[key]).lower(), (
                    f"[{action}] '{key}'={p[key]!r} does not contain '{contains}'.\n{msg}"
                )
            return
    pytest.fail(
        f"[{action}] never triggered.\n"
        f"Tetiklenenler: {result.all_triggered_types()}\n{msg}"
    )


def assert_no_raw_json(result: StreamResult, msg: str = ""):
    t = result.text
    brace_count = t.count("{") + t.count("}")
    quote_count = t.count('"')
    assert not (brace_count > 10 and quote_count > 20), (
        f"LLM dumped raw JSON content.\nText: {t[:600]}\n{msg}"
    )


def assert_text_not_empty(result: StreamResult, msg: str = "", min_chars: int = 40):
    assert len(result.text.strip()) >= min_chars, (
        f"LLM response too short ({len(result.text)} chars).\n"
        f"Metin: {result.text!r}\n{msg}"
    )


def assert_has_number(result: StreamResult, msg: str = ""):
    has_num = bool(re.search(r"\d[\d,.]*", result.text))
    assert has_num, (
        f"No number at all in the LLM response.\nText: {result.text[:400]}\n{msg}"
    )


def assert_mentions(result: StreamResult, keywords: list[str], msg: str = "", min_hits: int = 1):
    lower = result.text_lower()
    hits = [kw for kw in keywords if kw.lower() in lower]
    assert len(hits) >= min_hits, (
        f"LLM text contains none of: {keywords}\n"
        f"Metin: {result.text[:500]}\n{msg}"
    )


def assert_valid_timeframe(result: StreamResult, action: str, msg: str = ""):
    p = result.params_for(action)
    if "timeframe" in p:
        tf = p["timeframe"]
        assert tf in VALID_TIMEFRAMES, (
            f"Invalid timeframe '{tf}' was sent. Valid: {VALID_TIMEFRAMES}\n{msg}"
        )


def assert_no_timestamp_sent(result: StreamResult, action: str, msg: str = ""):
    p = result.params_for(action)
    bad = [k for k in ("startTime", "endTime", "start_time", "end_time") if k in p]
    assert not bad, (
        f"[{action}] the prompt forbids sending timestamps, yet "
        f"{bad} were sent.\nParams: {p}\n{msg}"
    )


def assert_no_query_onchain(result: StreamResult, msg: str = ""):
    triggered = result.all_triggered_types()
    assert "query_onchain" not in triggered, (
        f"query_onchain was triggered (for a timestamp). Triggered: {triggered}\n{msg}"
    )


def assert_explains_error(result: StreamResult, msg: str = ""):
    """Did the LLM explain the error to the user in prose?"""
    error_keywords = [
        "hata", "bulunamadı", "geçersiz", "error", "invalid", "not found",
        "unable", "cannot", "üzgün", "maalesef", "sorry"
    ]
    lower = result.text_lower()
    found = any(kw in lower for kw in error_keywords)
    assert found or result.asked_clarification(), (
        f"LLM produced no explanatory text on error.\n"
        f"Metin: {result.text[:400]}\n{msg}"
    )


def assert_asks_for_missing_param(result: StreamResult, msg: str = ""):
    """Did the LLM ask for clarification about the missing required parameter?"""
    assert result.asked_clarification() or result.has_text_content(), (
        f"For the missing parameter the LLM neither asked nor explained.\n"
        f"Metin: {result.text[:300]}\n{msg}"
    )


def assert_does_not_hallucinate_data(result: StreamResult, msg: str = ""):
    """Did the LLM claim it has no real-time access instead of fetching data?"""
    hallucination_phrases = [
        "gerçek zamanlı veriye erişimim yok",
        "güncel veriye sahip değilim",
        "i don't have access to real-time",
        "i cannot access live",
        "as of my knowledge cutoff",
    ]
    lower = result.text_lower()
    for phrase in hallucination_phrases:
        assert phrase not in lower, (
            f"LLM answered 'I have no access' without fetching data (hallucination).\n"
            f"Metin: {result.text[:400]}\n{msg}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 1. GET POOLS — 18 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2GetPools:

    def test_genel_liste_turkce(self, chat_client):
        msg = "Meteora DAMM v2 havuzlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_genel_liste_ingilizce(self, chat_client):
        msg = "List Meteora DAMM v2 pools"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_usdc_filtresi_ve_param(self, chat_client):
        msg = "Meteora DAMM v2'de USDC havuzlarını bul"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_param_present(r, "meteora_dammv2_get_pools", "query", "USDC", msg)
        assert_no_raw_json(r, msg)

    def test_sol_filtresi_ve_param(self, chat_client):
        msg = "Meteora DAMM v2 SOL içeren havuzları göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_param_present(r, "meteora_dammv2_get_pools", "query", "SOL", msg)

    def test_bonk_filtresi(self, chat_client):
        msg = "Meteora DAMM v2'de BONK havuzları var mı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_param_present(r, "meteora_dammv2_get_pools", "query", "BONK", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_volume_sirali_param_dogrulama(self, chat_client):
        msg = "Meteora DAMM v2 havuzlarını 24 saatlik hacme göre büyükten küçüğe sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        p = r.params_for("meteora_dammv2_get_pools")
        sort_val = str(p.get("sortBy", p.get("sort_by", ""))).lower()
        assert "volume" in sort_val or "desc" in sort_val, (
            f"sortBy should contain volume:desc, got: {sort_val!r}"
        )

    def test_tvl_sirali_param_dogrulama(self, chat_client):
        msg = "Meteora DAMM v2 havuzlarını TVL'ye göre sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        p = r.params_for("meteora_dammv2_get_pools")
        sort_val = str(p.get("sortBy", p.get("sort_by", ""))).lower()
        assert "tvl" in sort_val, f"sortBy should contain tvl, got: {sort_val!r}"

    def test_sayfalama_param(self, chat_client):
        msg = "Meteora DAMM v2 havuzları 2. sayfayı göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        p = r.params_for("meteora_dammv2_get_pools")
        assert str(p.get("page", "")) == "2", f"page=2 beklendi: {p}"

    def test_yorum_token_adlari_icermeli(self, chat_client):
        msg = "Meteora DAMM v2'deki en likit 3 havuzu ve hangi token çiftlerinden oluştuğunu açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        # The LLM should surface the token names in its answer
        assert_mentions(r, ["sol", "usdc", "usdt", "bonk", "jup", "/", "-"], msg)

    def test_yorum_tvl_sayisal_deger(self, chat_client):
        msg = "Meteora DAMM v2 havuzlarını TVL'ye göre listele ve en büyüğünün TVL'sini söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        # The LLM should state the TVL, in dollars or as a number
        assert_mentions(r, ["$", "tvl", "milyon", "million", "usd", "m"], msg)

    def test_yorum_volume_dolar_degeri(self, chat_client):
        msg = "Meteora DAMM v2'de 24 saatlik en yüksek işlem hacmine sahip havuzu bul ve hacmini söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_fee_tvl_orani_yorum(self, chat_client):
        msg = "Meteora DAMM v2'de fee/TVL oranı en yüksek havuzlar hangileri? Gelir potansiyelini analiz et"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "tvl", "oran", "ratio", "gelir", "income", "%"], msg)

    def test_farm_apr_bahsedilmeli(self, chat_client):
        msg = "Meteora DAMM v2'de farm APR'ı yüksek havuzlar hangileri?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["farm", "apr", "apy", "staking", "ödül", "reward", "%"], msg)

    def test_filter_by_parametresi(self, chat_client):
        msg = "Meteora DAMM v2'de TVL'si 100.000 dolardan yüksek havuzları listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_combined_query_and_sort(self, chat_client):
        msg = "Meteora DAMM v2'de SOL içeren havuzları hacme göre sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        p = r.params_for("meteora_dammv2_get_pools")
        assert p.get("query") or p.get("query") is not None
        assert_no_raw_json(r, msg)

    def test_sonuc_yok_durumu_yonetimi(self, chat_client):
        msg = "Meteora DAMM v2'de XXXXXXNOTEXIST token havuzları var mı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # No results -> the LLM should say so, or fall back to the general list

    def test_populer_havuzlar_ingilizce_yorum(self, chat_client):
        msg = "What are the most popular Meteora DAMM v2 pools right now? Explain briefly."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_yeni_dynamic_amm_v2_tanimi(self, chat_client):
        msg = "Meteora'nın yeni Dynamic AMM v2 nedir, hangi havuzları var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should explain DAMM v2
        assert_mentions(r, ["damm", "dynamic", "amm", "v2", "meteora"], msg)


# ══════════════════════════════════════════════════════════════════════════════
# 2. GET POOL GROUPS — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2GetPoolGroups:

    def test_genel_gruplar_turkce(self, chat_client):
        msg = "Meteora DAMM v2 token çifti gruplarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_volume_sirali_param(self, chat_client):
        msg = "Meteora DAMM v2 pool gruplarını 24 saatlik hacme göre sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        p = r.params_for("meteora_dammv2_get_pool_groups")
        sort_val = str(p.get("sortBy", p.get("sort_by", ""))).lower()
        assert "volume" in sort_val, f"sortBy should contain volume: {sort_val!r}"

    def test_tvl_sirali_param(self, chat_client):
        msg = "Meteora DAMM v2 pool groups sorted by TVL descending"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        p = r.params_for("meteora_dammv2_get_pool_groups")
        sort_val = str(p.get("sortBy", p.get("sort_by", ""))).lower()
        assert "tvl" in sort_val, f"sortBy should contain tvl: {sort_val!r}"

    def test_sayfalama_param(self, chat_client):
        msg = "Meteora DAMM v2 pool group 2. sayfa"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        p = r.params_for("meteora_dammv2_get_pool_groups")
        assert str(p.get("page", "")) == "2", f"page=2 beklendi: {p}"

    def test_volume_tw_7d_param(self, chat_client):
        msg = "Meteora DAMM v2 gruplarını 7 günlük hacme göre sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_fee_tvl_ratio_tw_param(self, chat_client):
        msg = "Meteora DAMM v2'de fee/TVL oranı en yüksek token çiftleri - son 24 saat"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_query_sol_gruplari(self, chat_client):
        msg = "Meteora DAMM v2'de SOL içeren token çifti grupları"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)

    def test_yorum_token_cifti_adlari(self, chat_client):
        msg = "Meteora DAMM v2'de en yüksek hacme sahip 5 token çifti grubunu söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        # The LLM should have listed the token-pair names
        assert_mentions(r, ["sol", "usdc", "usdt", "bonk", "jup", "/", "-"], msg)

    def test_yorum_pool_count(self, chat_client):
        msg = "Meteora DAMM v2'de her token çifti grubunda kaç havuz var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_yorum_tvl_deger(self, chat_client):
        msg = "Meteora DAMM v2 token çiftlerinde en büyük toplam TVL'ye sahip çift hangisi?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        assert_mentions(r, ["tvl", "$", "milyon", "million", "m", "usd"], msg)

    def test_yorum_farm_apr_grubu(self, chat_client):
        msg = "Meteora DAMM v2 gruplarında farm APR'ı en yüksek çiftler hangileri?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_has_farm_filtresi(self, chat_client):
        msg = "Meteora DAMM v2'de farm ödülü olan token çifti gruplarını bul"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["farm", "ödül", "reward", "apr", "apy"], msg)

    def test_ingilizce_analiz(self, chat_client):
        msg = "Which token pair groups have the highest fee income on Meteora DAMM v2?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "income", "group", "pair", "tvl"], msg)

    def test_hallucination_yok(self, chat_client):
        msg = "Meteora DAMM v2 pool gruplarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_karsilastirmali_yorum(self, chat_client):
        msg = "Meteora DAMM v2'de SOL-USDC ve SOL-USDT grupları arasında TVL karşılaştırması yap"
        r = _chat(chat_client, msg)
        # groups veya group endpointi tetiklenebilir
        triggered = r.all_triggered_types()
        assert any("dammv2" in t for t in triggered), (
            f"DAMM v2 endpoint beklendi. Tetiklenenler: {triggered}"
        )
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_sayfa_boyutu_param(self, chat_client):
        msg = "Meteora DAMM v2 pool gruplarının ilk 10'unu göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 3. GET POOL GROUP — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2GetPoolGroup:

    def test_sol_usdc_mint_ile(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2'de {mints} token çifti grubundaki havuzları listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_group", "lexicalOrderMints", msg=msg)
        assert_no_raw_json(r, msg)

    def test_sol_usdt_mint_ile(self, chat_client):
        mints = f"{SOL_MINT}-{USDT_MINT}"
        msg = f"Meteora DAMM v2 pool group: {mints}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_group", "lexicalOrderMints", msg=msg)

    def test_bonk_sol_mint_ile(self, chat_client):
        mints = f"{BONK_MINT}-{SOL_MINT}"
        msg = f"Bu mint çiftindeki tüm Meteora DAMM v2 havuzları: {mints}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_group", "lexicalOrderMints", msg=msg)

    def test_yorum_pool_count_grup(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2'de {mints} grubunda kaç havuz var ve TVL'leri nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_yorum_en_likit_havuz_grupta(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2 {mints} grubundaki en likit havuzu bul"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_sortby_volume_param(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2 {mints} grubunu hacme göre sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        p = r.params_for("meteora_dammv2_get_pool_group")
        sort_val = str(p.get("sortBy", p.get("sort_by", ""))).lower()
        assert "volume" in sort_val or sort_val == "", msg  # a sort may or may not have been sent

    def test_sayfa_param_grupta(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2 {mints} grubu 2. sayfa"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_no_raw_json(r, msg)

    def test_lexical_order_mints_zorunlu_bos_olmamali(self, chat_client):
        msg = "Meteora DAMM v2'de bir token çifti grubunu göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool_group" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_group")
            assert p.get("lexicalOrderMints"), (
                f"lexicalOrderMints must not be empty, got: {p}"
            )

    def test_mint_olmadan_clarify_beklenir(self, chat_client):
        msg = "Meteora DAMM v2 SOL-USDC grubundaki havuzları listele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        # If it cannot resolve the names to mints, clarifying or listing is acceptable
        # What matters: no call with an empty or invalid lexicalOrderMints
        if "meteora_dammv2_get_pool_group" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_group")
            assert p.get("lexicalOrderMints"), "lexicalOrderMints must not be empty"

    def test_ingilizce_group(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Get all Meteora DAMM v2 pools in group {mints} sorted by TVL"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_group", "lexicalOrderMints", msg=msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_yorum_tvl_sirali_havuzlar_grupta(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = (
            f"Meteora DAMM v2 {mints} grubundaki havuzları TVL'ye göre sırala "
            f"ve en büyüğünü açıkla"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_hallucination_yok_grup(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2 pool group {mints}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_does_not_hallucinate_data(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 4. GET POOL — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2GetPool:

    def test_sol_usdc_detay(self, chat_client):
        msg = f"Meteora DAMM v2 pool detayı: {DV2_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_param_present(r, "meteora_dammv2_get_pool", "address", DV2_SOL_USDC, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_sol_usdt_detay(self, chat_client):
        msg = f"Meteora DAMM v2 pool bilgisi {DV2_SOL_USDT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_param_present(r, "meteora_dammv2_get_pool", "address", DV2_SOL_USDT, msg)

    def test_bonk_sol_detay(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_BONK_SOL} havuzunun TVL ve fiyatını öğren"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_param_present(r, "meteora_dammv2_get_pool", "address", DV2_BONK_SOL, msg)

    def test_yorum_tvl_dolar(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} TVL'si ne kadar? Dolar cinsinden söyle."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        assert_mentions(r, ["$", "tvl", "milyon", "million", "dolar", "usd", "m"], msg)

    def test_yorum_guncel_fiyat(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} anlık SOL/USDC fiyatı nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        # The LLM should answer with the price

    def test_yorum_farm_apr(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} havuzunda farm APR'ı var mı? Varsa ne kadar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["farm", "apr", "apy", "ödül", "reward", "yok", "no", "yüzde", "%"], msg)

    def test_yorum_volume_24h(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} dünkü işlem hacmi ne kadardı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        assert_mentions(r, ["volume", "hacim", "24h", "24 saat", "$", "m"], msg)

    def test_yorum_token_bilgisi(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} hangi tokenleri içeriyor? Miktarlarını söyle."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["sol", "usdc", "token"], msg)

    def test_yorum_likidite_degerlendirmesi(self, chat_client):
        msg = f"Meteora DAMM v2 havuzuna {DV2_SOL_USDC} likidite eklemeli miyim? Analiz et."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        # The LLM should fetch real data and analyse it
        assert_does_not_hallucinate_data(r, msg)

    def test_ingilizce_pool_detail(self, chat_client):
        msg = f"What is the current state of Meteora DAMM v2 pool {DV2_SOL_USDC}? Include TVL and fees."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_gecersiz_adres_graceful(self, chat_client):
        msg = f"Meteora DAMM v2 pool bilgisi: {GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        # Either the LLM rejects the address, or the API returned an error
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # On error the user must get an explanation
        assert_explains_error(r, msg)

    def test_adres_olmadan_clarify(self, chat_client):
        msg = "Meteora DAMM v2 havuzunun detaylarını göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool" in triggered:
            p = r.params_for("meteora_dammv2_get_pool")
            assert p.get("address"), f"Pool call made without an address: {p}"

    def test_hallucination_yok(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} hakkında bilgi ver"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_adres_param_tam_eslesmeli(self, chat_client):
        msg = f"Get details for Meteora DAMM v2 pool at address {DV2_BONK_SOL}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        p = r.params_for("meteora_dammv2_get_pool")
        assert p.get("address") == DV2_BONK_SOL, (
            f"Address must match exactly. Expected: {DV2_BONK_SOL}, got: {p.get('address')!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5. GET POOL OHLCV — 18 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2GetPoolOhlcv:

    def test_temel_ohlcv(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} OHLCV verisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "address", DV2_SOL_USDC, msg)
        assert_valid_timeframe(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_no_raw_json(r, msg)

    def test_1h_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 1 saatlik mum grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "timeframe", "1h", msg)

    def test_5m_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 5 dakikalık mum verisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "timeframe", "5m", msg)

    def test_30m_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} 30 dakikalık OHLCV"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "timeframe", "30m", msg)

    def test_4h_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 4 saatlik candlestick"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "timeframe", "4h", msg)

    def test_12h_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} 12 saatlik OHLCV verisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "timeframe", "12h", msg)

    def test_24h_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} günlük mum grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        p = r.params_for("meteora_dammv2_get_pool_ohlcv")
        tf = p.get("timeframe", "24h")
        assert tf in VALID_TIMEFRAMES, f"Invalid timeframe: {tf!r}"

    def test_timestamp_kesinlikle_gonderilmemeli(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} son OHLCV verilerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_no_timestamp_sent(r, "meteora_dammv2_get_pool_ohlcv", msg)

    def test_query_onchain_cagrilmamali(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} en son mum verilerini göster"
        r = _chat(chat_client, msg)
        assert_no_query_onchain(r, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)

    def test_gecersiz_timeframe_gecerli_secmeli(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} aylık OHLCV"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_valid_timeframe(r, "meteora_dammv2_get_pool_ohlcv", msg)

    def test_haftalik_timeframe_en_yakin_secmeli(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} haftalık mum grafikleri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_valid_timeframe(r, "meteora_dammv2_get_pool_ohlcv", msg)

    def test_yorum_fiyat_hareketi(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} son 1 saatlik fiyat hareketi nasıl? Yükseldi mi düştü mü?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)
        # The LLM should comment on the price direction or the values
        assert_mentions(r, ["open", "close", "high", "low", "fiyat", "price", "yüksel", "düş", "artış", "azal"], msg)

    def test_yorum_ohlcv_sayisal_degerler(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 1h OHLCV — açılış ve kapanış fiyatlarını söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_sol_usdt_ohlcv(self, chat_client):
        msg = f"Meteora DAMM v2 SOL-USDT pool {DV2_SOL_USDT} 1 saatlik mum grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "address", DV2_SOL_USDT, msg)

    def test_ingilizce_candlestick(self, chat_client):
        msg = f"Get 1-hour OHLCV candlestick data for Meteora DAMM v2 pool {DV2_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "timeframe", "1h", msg)
        assert_no_raw_json(r, msg)

    def test_adres_olmadan_clarify(self, chat_client):
        msg = "Meteora DAMM v2 OHLCV verisini göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool_ohlcv" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_ohlcv")
            assert p.get("address"), f"OHLCV call made without an address: {p}"

    def test_bonk_sol_ohlcv(self, chat_client):
        msg = f"Meteora DAMM v2 BONK-SOL pool {DV2_BONK_SOL} 4 saatlik fiyat grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "address", DV2_BONK_SOL, msg)
        assert_param_present(r, "meteora_dammv2_get_pool_ohlcv", "timeframe", "4h", msg)

    def test_hallucination_yok_ohlcv(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 1h mum verilerini analiz et"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_does_not_hallucinate_data(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 6. GET POOL VOLUME HISTORY — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2GetPoolVolHistory:

    def test_temel_hacim_gecmisi(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} hacim geçmişi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_volume_history", "address", DV2_SOL_USDC, msg)
        assert_valid_timeframe(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_no_raw_json(r, msg)

    def test_1h_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} saatlik hacim geçmişi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_volume_history", "timeframe", "1h", msg)

    def test_5m_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 5 dakikalık hacim grafikleri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_volume_history", "timeframe", "5m", msg)

    def test_24h_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} günlük hacim grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        p = r.params_for("meteora_dammv2_get_pool_volume_history")
        tf = p.get("timeframe", "24h")
        assert tf in VALID_TIMEFRAMES, f"Invalid timeframe: {tf!r}"

    def test_4h_timeframe_param(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 4 saatlik trading volume history"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_volume_history", "timeframe", "4h", msg)

    def test_timestamp_kesinlikle_gonderilmemeli(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} son hacim verilerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_no_timestamp_sent(r, "meteora_dammv2_get_pool_volume_history", msg)

    def test_query_onchain_cagrilmamali(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} en güncel hacim verileri"
        r = _chat(chat_client, msg)
        assert_no_query_onchain(r, msg)

    def test_gecersiz_timeframe_gecerli_secmeli(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} haftalık hacim geçmişi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_valid_timeframe(r, "meteora_dammv2_get_pool_volume_history", msg)

    def test_yorum_hacim_trendi(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} hacim trendi nasıl gidiyor son saatlerde?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_yorum_hacim_sayisal(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} son 1 saatteki işlem hacmini söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_yorum_fee_gecmisi(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} son 24 saatte ne kadar fee üretti?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "ücret", "gelir", "income", "$", "m", "k"], msg)

    def test_sol_usdt_volume_history(self, chat_client):
        msg = f"Meteora DAMM v2 SOL-USDT pool {DV2_SOL_USDT} volume history 1h"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_volume_history", "address", DV2_SOL_USDT, msg)

    def test_bonk_sol_volume_history(self, chat_client):
        msg = f"Meteora DAMM v2 BONK-SOL {DV2_BONK_SOL} saatlik hacim verileri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_volume_history", "address", DV2_BONK_SOL, msg)

    def test_ingilizce_volume_history(self, chat_client):
        msg = f"Get historical volume data for Meteora DAMM v2 pool {DV2_SOL_USDC} with 1-hour intervals"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dammv2_get_pool_volume_history", "timeframe", "1h", msg)
        assert_no_raw_json(r, msg)

    def test_adres_olmadan_clarify(self, chat_client):
        msg = "Meteora DAMM v2 hacim geçmişini göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool_volume_history" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_volume_history")
            assert p.get("address"), f"Volume-history call without an address: {p}"

    def test_hallucination_yok(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} hacim verilerini analiz et"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_does_not_hallucinate_data(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 7. GET PROTOCOL METRICS — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2GetProtocolMetrics:

    def test_toplam_tvl(self, chat_client):
        msg = "Meteora DAMM v2 protokolünün toplam TVL'si ne kadar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        assert_mentions(r, ["tvl", "$", "milyon", "million", "dolar", "usd", "m", "b"], msg)

    def test_24h_volume(self, chat_client):
        msg = "Meteora DAMM v2 protokolünün 24 saatlik işlem hacmi nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["volume", "hacim", "24h", "24 saat", "$"], msg)

    def test_24h_fee(self, chat_client):
        msg = "Meteora DAMM v2 dün ne kadar fee topladı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "ücret", "gelir", "$", "24h"], msg)

    def test_toplam_pool_sayisi(self, chat_client):
        msg = "Meteora DAMM v2'de toplam kaç havuz var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["havuz", "pool", "adet", "toplam", "total"], msg)

    def test_toplam_fee_butun_zamanlar(self, chat_client):
        msg = "Meteora DAMM v2 şimdiye kadar toplam ne kadar fee üretti?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_protokol_ozeti_kapsamli(self, chat_client):
        msg = "Meteora DAMM v2 protokolünün genel istatistiklerini ver — TVL, 24h hacim, fee, havuz sayısı"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        # Should cover every headline metric
        assert_mentions(r, ["tvl", "volume", "fee", "pool", "havuz"], msg, min_hits=3)

    def test_parametresiz_cagrilmali(self, chat_client):
        msg = "Meteora DAMM v2 global statistics"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        p = r.params_for("meteora_dammv2_get_protocol_metrics")
        assert not p, f"Protocol metrics must be called with no params, got: {p}"

    def test_ingilizce_kapsamli(self, chat_client):
        msg = "What is the total TVL, 24h volume, and fee generated by Meteora DAMM v2 protocol?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_protokol_buyuklugu_yorumu(self, chat_client):
        msg = "Meteora DAMM v2 ne kadar büyük bir protokol? TVL ve pool sayısına göre analiz et."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_toplam_volume_tum_zamanlar(self, chat_client):
        msg = "Meteora DAMM v2 protokolü tüm zamanlar içinde toplam ne kadar işlem hacmi üretiyor?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_hallucination_yok_protocol(self, chat_client):
        msg = "Meteora DAMM v2 protocol metrics"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_yorum_karsilastirmali_zaman(self, chat_client):
        msg = "Meteora DAMM v2 bugün mi daha fazla hacim yaptı yoksa genel ortalamaya göre nasıl?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 8. ROUTING — DLMM vs DAMM v2 — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2Routing:

    def test_v2_keyword_pools(self, chat_client):
        msg = "Meteora DAMM v2 havuzlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_not_triggered(r, "meteora_dlmm_get_pairs", msg)

    def test_dlmm_keyword_pairs(self, chat_client):
        msg = "Meteora DLMM havuzlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_not_triggered(r, "meteora_dammv2_get_pools", msg)

    def test_v2_ohlcv_dogru_endpoint(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} OHLCV"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_not_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)

    def test_dlmm_ohlcv_dogru_endpoint(self, chat_client):
        msg = f"Meteora DLMM pool {DV2_SOL_USDC} için mum grafiği"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_pool_ohlcv" in triggered, (
            f"DLMM OHLCV beklendi. Tetiklenenler: {triggered}"
        )
        assert "meteora_dammv2_get_pool_ohlcv" not in triggered

    def test_v2_protocol_metrics_dogru(self, chat_client):
        msg = "Meteora DAMM v2 protokol istatistikleri — TVL ve hacim"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_not_triggered(r, "meteora_dlmm_get_protocol_stats", msg)

    def test_dlmm_protocol_stats_dogru(self, chat_client):
        msg = "Meteora DLMM protokolünün toplam TVL'si ne kadar?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_protocol_stats" in triggered, (
            f"DLMM protocol stats beklendi. Tetiklenenler: {triggered}"
        )
        assert "meteora_dammv2_get_protocol_metrics" not in triggered

    def test_v2_groups_dogru_endpoint(self, chat_client):
        msg = "Meteora DAMM v2 pool gruplarını TVL'e göre sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_groups", msg)
        assert_not_triggered(r, "meteora_dlmm_get_pool_groups", msg)

    def test_v2_volume_history_dogru(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC} hacim geçmişi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_not_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)

    def test_v2_pool_detail_dogru(self, chat_client):
        msg = f"Meteora DAMM v2 pool detayı: {DV2_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_not_triggered(r, "meteora_dlmm_get_pair", msg)

    def test_v1_keyword_v1_gitmeli(self, chat_client):
        msg = "Meteora Dynamic AMM v1 havuzlarını listele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t or "damm_v1" in t for t in triggered), (
            f"DAMM v1 endpoint beklendi. Tetiklenenler: {triggered}"
        )
        assert not any("dammv2" in t for t in triggered), (
            f"v1 sorusunda v2 tetiklendi: {triggered}"
        )

    def test_dynamic_amm_v2_pools(self, chat_client):
        msg = "Meteora'nın yeni Dynamic AMM v2 havuzları"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv2" in t for t in triggered), (
            f"DAMM v2 beklendi. Tetiklenenler: {triggered}"
        )

    def test_belirsiz_meteora_her_iki_kabul(self, chat_client):
        msg = "Meteora'da SOL-USDC havuzlarını listele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("meteora" in t for t in triggered), (
            f"Herhangi bir Meteora endpoint beklendi. Tetiklenenler: {triggered}"
        )

    def test_v2_pool_group_dogru_endpoint(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2 {mints} grubu"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_group", msg)
        assert_not_triggered(r, "meteora_dlmm_get_pool_group", msg)

    def test_v2_ingilizce_routing(self, chat_client):
        msg = "Show me Meteora DAMM v2 pools sorted by TVL"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_not_triggered(r, "meteora_dlmm_get_pairs", msg)

    def test_v2_vs_v1_protocol_metrics(self, chat_client):
        msg = "Meteora DAMM v2 global stats — NOT v1"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dammv2_get_protocol_metrics" in triggered, (
            f"DAMM v2 protocol metrics beklendi. Tetiklenenler: {triggered}"
        )
        assert "meteora_dammv1" not in " ".join(triggered)

    def test_both_protocols_ayri_sorgu(self, chat_client):
        msg = "Hem DLMM hem DAMM v2 havuzlarında SOL içerenleri bul"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        # The LLM may query both protocols or pick one
        assert any("meteora" in t for t in triggered), (
            f"Meteora endpoint beklendi. Tetiklenenler: {triggered}"
        )
        assert_text_not_empty(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 9. ERROR HANDLING — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2ErrorHandling:

    def test_gecersiz_pool_adresi_garbage(self, chat_client):
        msg = f"Meteora DAMM v2 pool: {GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_explains_error(r, msg)

    def test_gecersiz_pool_adresi_kisa(self, chat_client):
        msg = f"Meteora DAMM v2 pool {SHORT_ADDR} detayı"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_explains_error(r, msg)

    def test_varolmayan_pool_adresi(self, chat_client):
        msg = f"Meteora DAMM v2 pool {FAKE_ADDR} bilgisi"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should either say it found nothing, or relay the service error
        assert_explains_error(r, msg)

    def test_gecersiz_timeframe_ohlcv(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} için yıllık OHLCV (1y timeframe)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        # The LLM should substitute a valid timeframe
        assert_valid_timeframe(r, "meteora_dammv2_get_pool_ohlcv", msg)

    def test_gecersiz_timeframe_volume_history(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} aylık hacim geçmişi (30d timeframe)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_valid_timeframe(r, "meteora_dammv2_get_pool_volume_history", msg)

    def test_timestamp_iceren_sorgu_timestamp_gonderilmemeli(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 1h OHLCV — şu andan itibaren son 24 saat"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_no_timestamp_sent(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_no_query_onchain(r, msg)

    def test_bos_lexical_order_mints_reddedilmeli(self, chat_client):
        msg = "Meteora DAMM v2 pool group bilgisi ver (mint adresleri yok)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool_group" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_group")
            assert p.get("lexicalOrderMints"), "Called with an empty lexicalOrderMints"

    def test_api_hatasi_graceful_yonetimi(self, chat_client):
        # Valid format but a non-existent address -> the API may return 404
        nonexistent = "A" * 44
        msg = f"Meteora DAMM v2 pool {nonexistent}"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_explains_error(r, msg)

    def test_desteklenmeyen_islem_aciklama(self, chat_client):
        msg = "Meteora DAMM v2'de limit order açmak istiyorum"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should state that limit orders are unsupported
        lower = r.text_lower()
        assert any(kw in lower for kw in [
            "desteklenmez", "mevcut değil", "not supported", "limit order",
            "swap", "jupiter", "alternatif"
        ]), f"LLM gave no explanation for the unsupported operation.\nText: {r.text[:400]}"

    def test_yetersiz_parametre_ohlcv_adressiz(self, chat_client):
        msg = "Meteora DAMM v2 1h OHLCV verisini göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool_ohlcv" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_ohlcv")
            assert p.get("address"), f"OHLCV call without an address: {p}"
        else:
            # LLM clarify istedi — bu da kabul edilir
            assert_asks_for_missing_param(r, msg)

    def test_group_endpoint_url_injection_korunma(self, chat_client):
        # Attempt to smuggle special characters into lexicalOrderMints
        msg = "Meteora DAMM v2 pool group: ../../../etc/passwd-USDC"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM must not forward this input verbatim
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool_group" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_group")
            mints = p.get("lexicalOrderMints", "")
            assert "../" not in mints, f"Path-traversal characters reached lexicalOrderMints: {mints}"

    def test_sayisal_olmayan_sayfa_numarasi(self, chat_client):
        msg = "Meteora DAMM v2 havuzları sayfaNumber=abc göster"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_negatif_sayfa_numarasi(self, chat_client):
        msg = "Meteora DAMM v2 havuzlarının -1. sayfasını göster"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_sol_usdc_ohlcv_servis_yaniti_yorumlanmali(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} son 1h mum verilerindeki en yüksek fiyatı söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The data returned by the API should be interpreted
        assert_does_not_hallucinate_data(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 10. USER GUIDANCE — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2UserGuidance:

    def test_adres_isteme_ohlcv_icin(self, chat_client):
        msg = "Meteora DAMM v2 mum grafiğini göster"
        r = _chat(chat_client, msg)
        # The LLM will either ask for an address or produce an explanation
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool_ohlcv" in triggered:
            p = r.params_for("meteora_dammv2_get_pool_ohlcv")
            assert p.get("address"), "OHLCV call without an address"

    def test_adres_isteme_pool_icin(self, chat_client):
        msg = "Meteora DAMM v2 havuzunun detaylarını ver"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv2_get_pool" in triggered:
            p = r.params_for("meteora_dammv2_get_pool")
            assert p.get("address"), "Pool-detail call without an address"

    def test_swap_routes_correctly(self, chat_client):
        msg = "Meteora DAMM v2'de SOL karşılığında USDC almak istiyorum"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should route the user for the swap
        triggered = r.all_triggered_types()
        text_lower = r.text_lower()
        is_swap = "meteora_dammv2_swap" in triggered
        mentions_swap = any(kw in text_lower for kw in ["swap", "takas", "değiştir", "exchange"])
        assert is_swap or mentions_swap, (
            f"No routing offered for the swap question.\nText: {r.text[:300]}"
        )

    def test_likidite_ekleme_yonlendirme(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC}'e likidite eklemek istiyorum"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        triggered = r.all_triggered_types()
        text_lower = r.text_lower()
        is_add_liq = "meteora_dammv2_add_liquidity" in triggered
        mentions_liq = any(kw in text_lower for kw in [
            "likidite", "liquidity", "ekle", "add", "amount", "miktar"
        ])
        assert is_add_liq or mentions_liq, (
            f"No routing offered for adding liquidity.\nText: {r.text[:300]}"
        )

    def test_likidite_cekme_yonlendirme(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} havuzundan likiditeyi geri çekmek istiyorum"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        triggered = r.all_triggered_types()
        text_lower = r.text_lower()
        is_remove = "meteora_dammv2_remove_liquidity" in triggered
        mentions_remove = any(kw in text_lower for kw in [
            "çek", "remove", "withdraw", "lp", "position", "pozisyon"
        ])
        assert is_remove or mentions_remove

    def test_timeframe_secimi_icin_yonlendirme(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} fiyat grafiği göster — hangi zaman dilimlerini seçebilirim?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should state the valid timeframe options
        lower = r.text_lower()
        valid_mentioned = any(tf in lower for tf in ["5m", "30m", "1h", "4h", "12h", "24h"])
        assert valid_mentioned, (
            f"LLM did not state the valid timeframe options.\nText: {r.text[:400]}"
        )

    def test_hangi_protokol_secmeli_sorusu(self, chat_client):
        msg = "Meteora DLMM mi yoksa DAMM v2 mi daha iyi likidite sağlayıcısı için?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        # The LLM should explain both protocols
        assert_mentions(r, ["dlmm", "damm", "v2", "likidite", "liquidity"], msg)

    def test_mint_adresi_gerekmesini_aciklama(self, chat_client):
        mints = f"{SOL_MINT}-{USDC_MINT}"
        msg = f"Meteora DAMM v2 pool group {mints} için lexicalOrderMints nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_api_yeteneklerini_aciklama(self, chat_client):
        msg = "Meteora DAMM v2 için hangi verileri sorgulayabilirsiniz?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["pool", "ohlcv", "volume", "tvl", "group", "protocol", "metric"], msg, min_hits=2)

    def test_yanlis_protocol_duzeltme(self, chat_client):
        msg = "Raydium DAMM v2 havuzlarını göster"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should either route to Meteora DAMM v2 or explain Raydium
        lower = r.text_lower()
        assert any(kw in lower for kw in [
            "meteora", "raydium", "farklı", "different", "protocol"
        ]), f"LLM did not address the protocol mix-up.\nText: {r.text[:300]}"

    def test_sorguyu_soyutlayip_dogrulama(self, chat_client):
        msg = "Hangi Meteora DAMM v2 havuzunda likidite eklemek daha mantıklı?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should fetch and interpret, or ask for clarification
        triggered = r.all_triggered_types()
        has_data = any("dammv2" in t for t in triggered)
        has_guidance = r.has_text_content()
        assert has_data or has_guidance, "LLM neither fetched data nor offered guidance"

    def test_cok_eski_tarih_icin_yonlendirme(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 2020 yılındaki fiyat grafiği"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The protocol did not exist in 2020, or there is no data — the LLM must handle it

    def test_fee_geliri_hesaplama_sorgusu(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC}'ye 10.000 dolar koyarsam ne kadar fee kazanırım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should fetch pool data and either estimate or explain
        triggered = r.all_triggered_types()
        assert any("dammv2" in t for t in triggered) or r.has_text_content()

    def test_pool_adresi_nerede_bulunur(self, chat_client):
        msg = "Meteora DAMM v2 pool adresini nereden öğrenebilirim?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # The LLM should route the user (via pool groups or the pools listing)
        lower = r.text_lower()
        assert any(kw in lower for kw in [
            "meteora", "pool", "group", "liste", "list", "address", "adres"
        ]), f"LLM did not help with finding a pool address.\nText: {r.text[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# 11. INTERPRETATION DEPTH — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV2InterpretationDepth:

    def test_pools_listing_is_summarised(self, chat_client):
        msg = "Meteora DAMM v2'deki en iyi 5 havuzu listele ve kısaca açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["sol", "usdc", "tvl", "volume", "fee"], msg, min_hits=2)
        assert_has_number(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_protocol_metrics_derinlemesine(self, chat_client):
        msg = "Meteora DAMM v2 protokolünü derinlemesine analiz et: TVL, hacim trendi, fee geliri ve kaç havuz aktif olduğunu söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_text_not_empty(r, msg, min_chars=120)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)
        assert_mentions(r, ["tvl", "volume", "fee", "pool", "havuz"], msg, min_hits=3)

    def test_pool_detayi_yatirim_yorumu(self, chat_client):
        msg = f"Meteora DAMM v2 pool {DV2_SOL_USDC}: LP olarak likidite eklemeli miyim? Gerçek verilere dayanarak analiz et."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)
        assert_has_number(r, msg)

    def test_ohlcv_up_down_interpretation(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} son 4 saatlik fiyat hareketini yorumla: yükseliş mi düşüş mü?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_volume_history_trend_yorumu(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} saatlik hacim verileri artıyor mu azalıyor mu?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)
        assert_mentions(r, [
            "artış", "azalış", "arttı", "azaldı", "increasing", "decreasing",
            "trend", "volume", "hacim", "yükseliş", "düşüş"
        ], msg)

    def test_karsilastirma_sol_usdc_vs_sol_usdt(self, chat_client):
        msg = (
            f"Meteora DAMM v2'de {DV2_SOL_USDC} (SOL-USDC) ve {DV2_SOL_USDT} (SOL-USDT) "
            f"havuzlarını karşılaştır — hangisinde daha fazla TVL ve işlem hacmi var?"
        )
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv2_get_pool" in t for t in triggered), (
            f"Pool detail endpoint beklendi. Tetiklenenler: {triggered}"
        )
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

    def test_fee_tvl_orani_hangi_havuz_daha_verimli(self, chat_client):
        msg = "Meteora DAMM v2 havuzları arasında fee geliri / TVL oranı en yüksek hangisi? Neden önemli?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "tvl", "oran", "ratio", "verimli", "efficient", "lp"], msg)

    def test_protocol_ile_dlmm_karsilastirma(self, chat_client):
        msg = "Meteora DAMM v2 ve DLMM protokollerini TVL açısından karşılaştır"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert len(triggered) >= 1, f"En az 1 endpoint tetiklenmeli. Tetiklenenler: {triggered}"
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_ohlcv_destek_direnc_yorumu(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} 1h OHLCV verilerinde destek ve direnç seviyeleri nerede görünüyor?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_volume_history_anomali_yorumu(self, chat_client):
        msg = f"Meteora DAMM v2 {DV2_SOL_USDC} saatlik hacim verilerinde ani bir hacim artışı var mı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_lp_strateji_onerisi(self, chat_client):
        msg = "Meteora DAMM v2'de en iyi LP stratejisi nedir? Gerçek verilere dayanarak öneri yap."
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        triggered = r.all_triggered_types()
        # The LLM should fetch data and suggest a strategy
        has_data = any("dammv2" in t for t in triggered)
        has_text = r.has_text_content()
        assert has_data or has_text

    def test_farm_apr_analizi(self, chat_client):
        msg = "Meteora DAMM v2'de farm APR'ı olan havuzları bul ve en yüksek APR'ı yorumla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["farm", "apr", "apy", "%", "ödül", "reward"], msg)

    def test_protocol_buyume_analizi(self, chat_client):
        msg = "Meteora DAMM v2 ne kadar hızlı büyüyor? TVL ve pool sayısına bakarak değerlendir."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv2_get_protocol_metrics", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_cok_adimli_soru_yanit(self, chat_client):
        msg = (
            f"Meteora DAMM v2 için şunları yap: "
            f"1) Protocol TVL'yi öğren, "
            f"2) En likit pool'un adresini bul, "
            f"3) O pool'un 1h fiyat grafiğini analiz et"
        )
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        triggered = r.all_triggered_types()
        assert any("dammv2" in t for t in triggered), (
            f"DAMM v2 endpoint beklendi. Tetiklenenler: {triggered}"
        )

"""
Meteora DAMM v1 — Kapsamlı LLM Kalite Testi

Bu testler yalnızca action routing'i değil, LLM'in:
  - Kullanıcı sorgularından doğru parametreleri çıkarıp çıkarmadığını
  - Filtreleme/sıralama parametrelerini doğru iletip iletmediğini
  - Ham JSON dökmeyip anlamlı metin üretip üretmediğini
  - Hataları/eksik parametreleri kullanıcıya nazikçe iletip iletmediğini
  - DAMM v1 ile DLMM/v2 arasındaki ayrımı doğru yapıp yapmadığını
  - Belirsiz sorgularda doğru yönlendirme yapıp yapmadığını
kontrol eder.

Test sınıfları:
  TestDV1GetPools             — 18 test (liste, pool_type, isMonitoring, hideLowTvl)
  TestDV1SearchPools          — 16 test (filter, sortKey, orderBy, sayfalama, yorum kalitesi)
  TestDV1GetFarms             — 10 test (parametresiz çağrı, farm yorumu)
  TestDV1GetPoolsMetrics      — 10 test (aggregate stats, parametresiz, yorum)
  TestDV1GetAlphaVaults       — 14 test (vault/pool/baseMint filtre, yorum)
  TestDV1GetAlphaVaultConfigs — 8 test  (config liste, parametresiz)
  TestDV1GetPoolConfigs       — 8 test  (fee tiers, parametresiz)
  TestDV1GetFeeConfig         — 10 test (adres gerektiren, eksik param)
  TestDV1Routing              — 16 test (DLMM vs v1, v2 vs v1, belirsiz)
  TestDV1ErrorHandling        — 12 test (geçersiz adres, eksik zorunlu param)
  TestDV1UserGuidance         — 12 test (clarification, yönlendirme, uyarı)
  TestDV1InterpretationDepth  — 12 test (analiz, karşılaştırma, kullanıcı yararı)

Toplam: ~146 test
"""

from __future__ import annotations

import json
import os
import re
import uuid

import httpx
import pytest

# ─── Servis yapılandırması ─────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://localhost:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

# Bilinen DAMM v1 havuz adresleri (mainnet, gerçek)
DV1_SOL_USDC  = "HcjZvfeSNJbNkfLD4eEcVBr987W65zqKvvcaYkM7SkAs"
DV1_SOL_USDT  = "32D4zRxNc1EssbJieVHfPhZM3rH6CzfUPrWTTkjHdgLw"
DV1_BONK_SOL  = "5rJhDkDQMmNn8D6mcGbFBq4dG7TSGcLFGMaqMJFJnygE"

SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

FAKE_ADDR    = "FakeAddr111111111111111111111111111111111111"
GARBAGE_ADDR = "not-a-solana-address"

VALID_POOL_TYPES = {"dynamic", "multitoken", "lst", "farms"}
VALID_SORT_KEYS  = {"tvl", "volume", "fee_tvl_ratio", "l_m"}
VALID_ORDER_BY   = {"asc", "desc"}


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def chat_client():
    try:
        r = httpx.get(f"{CHAT_URL}/health", timeout=3.0)
        if r.status_code not in (200, 204):
            pytest.skip(f"chat-service yanıt vermedi: HTTP {r.status_code}")
    except Exception as exc:
        pytest.skip(f"chat-service ulaşılamıyor ({CHAT_URL}): {exc}")
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


# ─── SSE stream ayrıştırıcı ───────────────────────────────────────────────

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


# ─── Doğrulama yardımcıları ───────────────────────────────────────────────

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
        f"İstenmeyen '{unwanted}' tetiklendi.\n"
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
                    f"[{action}] '{key}'={p[key]!r} içinde '{contains}' bulunamadı.\n{msg}"
                )
            return
    pytest.fail(
        f"[{action}] hiç tetiklenmedi.\n"
        f"Tetiklenenler: {result.all_triggered_types()}\n{msg}"
    )


def assert_param_absent(result: StreamResult, action: str, key: str, msg: str = ""):
    p = result.params_for(action)
    assert key not in p or p[key] is None, (
        f"[{action}] '{key}' gönderilmemeli ama gönderildi: {p[key]!r}\n{msg}"
    )


def assert_no_raw_json(result: StreamResult, msg: str = ""):
    t = result.text
    brace_count = t.count("{") + t.count("}")
    quote_count = t.count('"')
    assert not (brace_count > 10 and quote_count > 20), (
        f"LLM ham JSON içeriği döktü.\nMetin: {t[:600]}\n{msg}"
    )


def assert_text_not_empty(result: StreamResult, msg: str = "", min_chars: int = 40):
    assert len(result.text.strip()) >= min_chars, (
        f"LLM yanıtı çok kısa ({len(result.text)} karakter).\n"
        f"Metin: {result.text!r}\n{msg}"
    )


def assert_has_number(result: StreamResult, msg: str = ""):
    has_num = bool(re.search(r"\d[\d,.]*", result.text))
    assert has_num, (
        f"LLM yanıtında hiç sayı yok.\nMetin: {result.text[:400]}\n{msg}"
    )


def assert_mentions(result: StreamResult, keywords: list[str], msg: str = "", min_hits: int = 1):
    lower = result.text_lower()
    hits = [kw for kw in keywords if kw.lower() in lower]
    assert len(hits) >= min_hits, (
        f"LLM metni şunlardan hiçbirini içermiyor: {keywords}\n"
        f"Metin: {result.text[:500]}\n{msg}"
    )


def assert_explains_error(result: StreamResult, msg: str = ""):
    error_keywords = [
        "hata", "bulunamadı", "geçersiz", "error", "invalid", "not found",
        "unable", "cannot", "üzgün", "maalesef", "sorry"
    ]
    lower = result.text_lower()
    found = any(kw in lower for kw in error_keywords)
    assert found or result.asked_clarification(), (
        f"Hata durumunda LLM açıklayıcı metin üretmedi.\n"
        f"Metin: {result.text[:400]}\n{msg}"
    )


def assert_asks_for_missing_param(result: StreamResult, msg: str = ""):
    assert result.asked_clarification() or result.has_text_content(), (
        f"Eksik parametre için LLM ne soru sordu ne de açıklama yaptı.\n"
        f"Metin: {result.text[:300]}\n{msg}"
    )


def assert_does_not_hallucinate_data(result: StreamResult, msg: str = ""):
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
            f"LLM veri çekmeden 'erişimim yok' yanıtı verdi (hallucination).\n"
            f"Metin: {result.text[:400]}\n{msg}"
        )


def assert_valid_pool_type_sent(result: StreamResult, action: str, msg: str = ""):
    p = result.params_for(action)
    if "poolType" in p and p["poolType"]:
        pt = str(p["poolType"]).lower()
        assert pt in VALID_POOL_TYPES, (
            f"[{action}] Geçersiz poolType='{pt}'. Geçerliler: {VALID_POOL_TYPES}\n{msg}"
        )


def assert_valid_sort_key_sent(result: StreamResult, action: str, msg: str = ""):
    p = result.params_for(action)
    if "sortKey" in p and p["sortKey"]:
        sk = str(p["sortKey"]).lower()
        assert sk in VALID_SORT_KEYS, (
            f"[{action}] Geçersiz sortKey='{sk}'. Geçerliler: {VALID_SORT_KEYS}\n{msg}"
        )


def assert_pagination_params_present(result: StreamResult, action: str, msg: str = ""):
    p = result.params_for(action)
    assert "page" in p, f"[{action}] 'page' eksik. Params: {p}\n{msg}"
    assert "size" in p, f"[{action}] 'size' eksik. Params: {p}\n{msg}"
    try:
        assert int(p["page"]) >= 0, f"[{action}] page < 0\n{msg}"
        assert int(p["size"]) > 0,  f"[{action}] size <= 0\n{msg}"
    except (ValueError, TypeError):
        pytest.fail(f"[{action}] page/size sayısal değil: {p}\n{msg}")


# ══════════════════════════════════════════════════════════════════════════════
# 1. GET POOLS — 18 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1GetPools:

    def test_genel_liste_turkce(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_genel_liste_ingilizce(self, chat_client):
        msg = "List Meteora DAMM v1 pools"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_lst_pool_type_filtresi(self, chat_client):
        msg = "Meteora Dynamic AMM'de LST havuzlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_param_present(r, "meteora_dammv1_get_pools", "poolType", "lst", msg)
        assert_valid_pool_type_sent(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg)

    def test_multitoken_pool_type(self, chat_client):
        msg = "Meteora DAMM v1 multitoken havuzları göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_param_present(r, "meteora_dammv1_get_pools", "poolType", "multitoken", msg)
        assert_valid_pool_type_sent(r, "meteora_dammv1_get_pools", msg)

    def test_dynamic_pool_type(self, chat_client):
        msg = "Meteora Dynamic AMM standart dynamic havuzlarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_valid_pool_type_sent(r, "meteora_dammv1_get_pools", msg)

    def test_is_monitoring_filtresi(self, chat_client):
        msg = "Meteora DAMM v1 izlenen (monitored) havuzları listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_param_present(r, "meteora_dammv1_get_pools", "isMonitoring", msg=msg)

    def test_hide_low_tvl_filtresi(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını listele ama TVL'si 10000 dolardan düşük olanları gizle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_param_present(r, "meteora_dammv1_get_pools", "hideLowTvl", msg=msg)
        assert_text_not_empty(r, msg)

    def test_belirli_havuz_adresi_ile(self, chat_client):
        msg = f"Meteora Dynamic AMM'de {DV1_SOL_USDC} adresli havuzu göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_param_present(r, "meteora_dammv1_get_pools", "address", DV1_SOL_USDC, msg)

    def test_havuz_listesi_anlam_yorumu(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını göster ve en yüksek TVL'li olanı vurgula"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["tvl", "havuz", "pool"], msg)

    def test_unknown_havuzlar(self, chat_client):
        msg = "Meteora DAMM v1'de unverified/unknown havuzları da dahil et"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_param_present(r, "meteora_dammv1_get_pools", "unknown", msg=msg)

    def test_hide_low_apr_filtresi(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarından düşük APR'li olanları filtrele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_param_present(r, "meteora_dammv1_get_pools", "hideLowApr", msg=msg)

    def test_farm_pool_type(self, chat_client):
        msg = "Meteora DAMM v1 farms tipli havuzları getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_valid_pool_type_sent(r, "meteora_dammv1_get_pools", msg)

    def test_yorum_kalitesi_no_raw_json(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını anlayabileceğim şekilde özetle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=80)

    def test_havuz_sayisi_icermeli(self, chat_client):
        msg = "Kaç tane Meteora DAMM v1 havuzu var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg)

    def test_kombinasyon_lst_ve_monitoring(self, chat_client):
        msg = "Meteora Dynamic AMM'de izlenen LST havuzlarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_valid_pool_type_sent(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg)

    def test_launchpad_filtresi(self, chat_client):
        msg = "Meteora Dynamic AMM'de launchpad havuzlarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_sol_usdc_ciftine_gore(self, chat_client):
        msg = "Meteora Dynamic AMM'de SOL-USDC havuzu bilgilerini getir"
        r = _chat(chat_client, msg)
        # get_pools veya search_pools tetiklenebilir
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_dammv1_get_pools", "meteora_dammv1_search_pools"
        ]), f"Beklenen eylem tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        assert_text_not_empty(r, msg)

    def test_ham_json_yok_havuz_liste(self, chat_client):
        msg = "Meteora Dynamic AMM tüm havuzlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 2. SEARCH POOLS — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1SearchPools:

    def test_bonk_arama_filter_param(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarında BONK ara"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "filter", "BONK", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_usdc_arama(self, chat_client):
        msg = "Meteora DAMM v1'de USDC içeren havuzları ara"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "filter", "USDC", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_sol_arama_ingilizce(self, chat_client):
        msg = "Search Meteora Dynamic AMM pools for SOL"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "filter", "SOL", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_tvl_gore_siralama(self, chat_client):
        msg = "Meteora DAMM v1 havuzlarını TVL'ye göre büyükten küçüğe sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "sortKey", "tvl", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "orderBy", "desc", msg)
        assert_valid_sort_key_sent(r, "meteora_dammv1_search_pools", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_volume_gore_siralama(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını 24 saatlik hacme göre sırala (yüksekten düşüğe)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "sortKey", "volume", msg)
        assert_valid_sort_key_sent(r, "meteora_dammv1_search_pools", msg)

    def test_fee_tvl_ratio_siralama(self, chat_client):
        msg = "Meteora DAMM v1'de fee/TVL oranı en yüksek havuzları bul"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_valid_sort_key_sent(r, "meteora_dammv1_search_pools", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_sayfalama_ikinci_sayfa(self, chat_client):
        msg = "Meteora DAMM v1 havuz aramasının ikinci sayfasını göster (sayfa 1, 20'şer)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        p = r.params_for("meteora_dammv1_search_pools")
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)
        assert int(p.get("size", 0)) > 0, f"size pozitif olmalı: {p}\n{msg}"

    def test_asc_siralama(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını TVL'ye göre küçükten büyüğe sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "orderBy", "asc", msg)

    def test_pool_type_ile_arama(self, chat_client):
        msg = "Meteora DAMM v1'de LST tipli havuzları TVL'ye göre sıralı ara"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_valid_sort_key_sent(r, "meteora_dammv1_search_pools", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_arama_sonucu_yorum_kalitesi(self, chat_client):
        msg = "Meteora Dynamic AMM'de USDC içeren havuzları ara ve en iyi olanı öner"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_does_not_hallucinate_data(r, msg)

    def test_mint_adresiyle_arama(self, chat_client):
        msg = f"Meteora DAMM v1'de {BONK_MINT} mintini içeren havuzları ara"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_monitoring_filtreli_arama(self, chat_client):
        msg = "Meteora DAMM v1'de izlenen havuzları TVL'ye göre sıralı listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_valid_sort_key_sent(r, "meteora_dammv1_search_pools", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_hide_low_tvl_ile_arama(self, chat_client):
        msg = "Meteora DAMM v1'de BONK havuzlarını ara, TVL'si 5000 dolardan düşük olanları gizle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "filter", "BONK", msg)
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)

    def test_sonuc_no_raw_json(self, chat_client):
        msg = "Meteora Dynamic AMM'de SOL havuzlarını ara"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_no_raw_json(r, msg)

    def test_top10_havuz_mantikli_size(self, chat_client):
        msg = "Meteora DAMM v1'de en iyi 10 havuzu TVL'ye göre listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        p = r.params_for("meteora_dammv1_search_pools")
        assert_pagination_params_present(r, "meteora_dammv1_search_pools", msg)
        # LLM size=10 veya makul bir değer göndermeli
        size = int(p.get("size", 0))
        assert 5 <= size <= 50, f"size={size} beklenenden uzak (10 civarı bekleniyor)\n{msg}"

    def test_arama_yorum_anlam_yuk(self, chat_client):
        msg = "Meteora Dynamic AMM'de en çok hacim yapan havuz hangisi? Ara ve bana açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["hacim", "volume", "havuz", "pool"], msg)


# ══════════════════════════════════════════════════════════════════════════════
# 3. GET FARMS — 10 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1GetFarms:

    def test_farms_turkce(self, chat_client):
        msg = "Meteora Dynamic AMM farm programlarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_farms_ingilizce(self, chat_client):
        msg = "Show me Meteora DAMM v1 farms"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_text_not_empty(r, msg)

    def test_farms_parametresiz_cagri(self, chat_client):
        """Farms endpoint param almaz — LLM ekstra param göndermemeli"""
        msg = "Meteora DAMM v1 yield farming fırsatlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        p = r.params_for("meteora_dammv1_get_farms")
        # Boş veya yalnızca anlamsız opsiyonel alanlar olabilir
        essential_wrong = [k for k in p if k in ("pool_address", "poolAddress")]
        assert not essential_wrong, (
            f"Farms endpoint params almaz ama şunlar gönderildi: {essential_wrong}\nParams: {p}\n{msg}"
        )

    def test_farms_yorum_kalitesi(self, chat_client):
        msg = "Meteora Dynamic AMM'de farming yapabilir miyim? Ne var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_farms_no_hallucination(self, chat_client):
        msg = "Meteora DAMM v1 farm listesini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_farms_not_confused_with_dlmm(self, chat_client):
        msg = "Meteora Dynamic AMM (v1) farm programlarını göster, DLMM değil"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_not_triggered(r, "meteora_dlmm_get_positions", msg)

    def test_farms_yield_farming_kavrami(self, chat_client):
        msg = "Meteora'da likidite sağlayarak ödül kazanabilir miyim? Farm var mı?"
        r = _chat(chat_client, msg)
        # farms veya get_pools tetiklenebilir
        triggered = r.all_triggered_types()
        assert any("meteora" in t for t in triggered), (
            f"Meteora eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg)

    def test_farms_anlam_iceren_yanit(self, chat_client):
        msg = "Meteora Dynamic AMM farm programlarını açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_farms_ham_json_yok(self, chat_client):
        msg = "Meteora DAMM v1 farmlarını bana anlaşılır şekilde anlat"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_no_raw_json(r, msg)

    def test_farms_not_dammv2(self, chat_client):
        msg = "Meteora Dynamic AMM v1 farm programlarını getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_not_triggered(r, "meteora_dammv2_get_pools", msg)


# ══════════════════════════════════════════════════════════════════════════════
# 4. GET POOLS METRICS — 10 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1GetPoolsMetrics:

    def test_metrics_genel_turkce(self, chat_client):
        msg = "Meteora Dynamic AMM genel metrikleri nelerdir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_metrics_toplam_tvl(self, chat_client):
        msg = "Meteora DAMM v1'in toplam TVL'si ne kadar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_text_not_empty(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_metrics_parametresiz_cagri(self, chat_client):
        """Metrics endpoint param almaz"""
        msg = "Meteora Dynamic AMM protokol istatistiklerini göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        p = r.params_for("meteora_dammv1_get_pools_metrics")
        wrong = [k for k in p if k in ("poolAddresses", "pool_addresses")]
        assert not wrong, (
            f"Metrics endpoint params almaz ama şunlar gönderildi: {wrong}\nParams: {p}\n{msg}"
        )

    def test_metrics_yorum_kalitesi(self, chat_client):
        msg = "Meteora DAMM v1 ne kadar hacim yapıyor? Genel durumu nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_does_not_hallucinate_data(r, msg)

    def test_metrics_ingilizce(self, chat_client):
        msg = "Show Meteora DAMM v1 protocol metrics"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_text_not_empty(r, msg)

    def test_metrics_tvl_volume_apy_keywords(self, chat_client):
        msg = "Meteora Dynamic AMM'in genel TVL, hacim ve APY verilerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_mentions(r, ["tvl", "volume", "hacim", "apy", "apr"], msg)
        assert_no_raw_json(r, msg)

    def test_metrics_not_search(self, chat_client):
        msg = "Meteora Dynamic AMM genel protokol metrikleri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_not_triggered(r, "meteora_dammv1_search_pools", msg)

    def test_metrics_no_hallucination(self, chat_client):
        msg = "Meteora DAMM v1 aggregate istatistikleri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_metrics_kac_havuz(self, chat_client):
        msg = "Meteora Dynamic AMM'de toplam kaç havuz var?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_dammv1_get_pools_metrics", "meteora_dammv1_get_pools"
        ]), f"Beklenen eylem tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        assert_text_not_empty(r, msg)

    def test_metrics_ham_json_yok(self, chat_client):
        msg = "Meteora DAMM v1 istatistiklerini anlaşılır şekilde özetle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)


# ══════════════════════════════════════════════════════════════════════════════
# 5. GET ALPHA VAULTS — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1GetAlphaVaults:

    def test_alpha_vault_genel_liste(self, chat_client):
        msg = "Meteora Alpha Vault'larını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_alpha_vault_ingilizce(self, chat_client):
        msg = "Show Meteora Alpha Vaults"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_text_not_empty(r, msg)

    def test_alpha_vault_pool_adresiyle(self, chat_client):
        msg = f"Meteora DAMM v1 {DV1_SOL_USDC} havuzunun Alpha Vault'unu göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_param_present(r, "meteora_dammv1_get_alpha_vaults", "poolAddress", DV1_SOL_USDC, msg)

    def test_alpha_vault_base_mint_ile(self, chat_client):
        msg = f"Meteora Alpha Vault'larını SOL mint adresine ({SOL_MINT}) göre filtrele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_param_present(r, "meteora_dammv1_get_alpha_vaults", "baseMint", SOL_MINT, msg)

    def test_alpha_vault_yorum_kalitesi(self, chat_client):
        msg = "Meteora Alpha Vault nedir, nasıl çalışır? Mevcut vault'ları göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_alpha_vault_presale_kavram(self, chat_client):
        msg = "Meteora'da fair-launch veya pre-sale vault'ları var mı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_text_not_empty(r, msg)

    def test_alpha_vault_not_dlmm(self, chat_client):
        msg = "Meteora Alpha Vault listesini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_not_triggered(r, "meteora_dlmm_get_positions", msg)

    def test_alpha_vault_not_dammv2(self, chat_client):
        msg = "Meteora Dynamic AMM Alpha Vault'larını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_not_triggered(r, "meteora_dammv2_get_pools", msg)

    def test_alpha_vault_no_raw_json(self, chat_client):
        msg = "Meteora Alpha Vault'larını anlayabileceğim şekilde anlat"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_no_raw_json(r, msg)

    def test_alpha_vault_no_hallucination(self, chat_client):
        msg = "Mevcut Meteora Alpha Vault'larını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_alpha_vault_configs_ayrimi(self, chat_client):
        """Alpha vault listesi ile config presetleri karıştırılmamalı"""
        msg = "Meteora Alpha Vault'larının tümünü listele (config değil, vault listesi)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_dammv1_get_alpha_vaults",
            "meteora_dammv1_get_alpha_vault_configs",
        ]), f"Beklenen eylem tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        assert_text_not_empty(r, msg)

    def test_alpha_vault_token_acilayi(self, chat_client):
        msg = "Meteora'da token launch için Alpha Vault kullanabilir miyim?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_alpha_vault_vault_adresiyle(self, chat_client):
        msg = "Meteora Alpha Vault'unda belirli bir vault adresi ile filtrele"
        r = _chat(chat_client, msg)
        # LLM clarification isteyebilir (adres belirtilmediği için)
        assert result_has_response(r), f"LLM yanıt vermedi.\n{msg}"

    def test_alpha_vault_anlam_yorumu_dolu(self, chat_client):
        msg = "Meteora Alpha Vault'ları hakkında bilgi ver ve mevcut fırsatları göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)


def result_has_response(r: StreamResult) -> bool:
    return r.has_text_content() or r.asked_clarification() or len(r.all_triggered_types()) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 6. GET ALPHA VAULT CONFIGS — 8 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1GetAlphaVaultConfigs:

    def test_config_liste_turkce(self, chat_client):
        msg = "Meteora Alpha Vault konfigürasyon presetlerini listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_config_ingilizce(self, chat_client):
        msg = "Show Meteora Alpha Vault configuration presets"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        assert_text_not_empty(r, msg)

    def test_config_parametresiz_cagri(self, chat_client):
        """Alpha vault configs param almaz"""
        msg = "Meteora Alpha Vault config ayarları nelerdir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        p = r.params_for("meteora_dammv1_get_alpha_vault_configs")
        assert p == {} or all(v is None for v in p.values()), (
            f"Alpha vault configs param almaz ama gönderildi: {p}\n{msg}"
        )

    def test_config_yorum_kalitesi(self, chat_client):
        msg = "Meteora Alpha Vault config seçeneklerini açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_config_not_confused_with_pool_configs(self, chat_client):
        """Alpha vault configs ile pool configs karışmamalı"""
        msg = "Meteora Alpha Vault'un konfigürasyon seçenekleri"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_dammv1_get_alpha_vault_configs",
            "meteora_dammv1_get_pool_configs",
        ]), f"Config eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"

    def test_config_no_hallucination(self, chat_client):
        msg = "Meteora Alpha Vault preset konfigürasyonları getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_config_no_raw_json(self, chat_client):
        msg = "Meteora Alpha Vault hangi konfigürasyonları destekliyor?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        assert_no_raw_json(r, msg)

    def test_config_meaningful_explanation(self, chat_client):
        msg = "Meteora Alpha Vault config presetlerini kullanıcı dostu bir şekilde özetle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        assert_text_not_empty(r, msg, min_chars=80)


# ══════════════════════════════════════════════════════════════════════════════
# 7. GET POOL CONFIGS — 8 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1GetPoolConfigs:

    def test_pool_config_fee_tiers(self, chat_client):
        msg = "Meteora Dynamic AMM'de hangi fee tier'lar mevcut?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_pool_config_ingilizce(self, chat_client):
        msg = "Show Meteora DAMM v1 pool configuration presets"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        assert_text_not_empty(r, msg)

    def test_pool_config_parametresiz(self, chat_client):
        msg = "Meteora Dynamic AMM havuz konfigürasyonlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        p = r.params_for("meteora_dammv1_get_pool_configs")
        assert p == {} or all(v is None for v in p.values()), (
            f"Pool configs param almaz ama gönderildi: {p}\n{msg}"
        )

    def test_pool_config_anlam_yorumu(self, chat_client):
        msg = "Meteora Dynamic AMM'de havuz açmak için hangi konfigürasyonlar var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_does_not_hallucinate_data(r, msg)

    def test_pool_config_no_raw_json(self, chat_client):
        msg = "Meteora DAMM v1 havuz config presetlerini anlayabileceğim şekilde göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        assert_no_raw_json(r, msg)

    def test_pool_config_not_alpha_vault(self, chat_client):
        msg = "Meteora Dynamic AMM standart pool konfigürasyonları nelerdir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        assert_not_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)

    def test_pool_config_fee_keyword(self, chat_client):
        msg = "Meteora Dynamic AMM ücret (fee) yapılandırmaları"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_dammv1_get_pool_configs",
            "meteora_dammv1_get_fee_config",
        ]), f"Config/fee eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        assert_text_not_empty(r, msg)

    def test_pool_config_no_hallucination(self, chat_client):
        msg = "Meteora DAMM v1 havuz açmak için mevcut konfigürasyonlar"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        assert_does_not_hallucinate_data(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 8. ROUTING TESTS — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1Routing:

    def test_v1_vs_dlmm_routing(self, chat_client):
        msg = "Meteora Dynamic AMM (klasik) havuzları listele, DLMM değil"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_not_triggered(r, "meteora_dlmm_get_positions", msg)

    def test_v1_vs_v2_routing(self, chat_client):
        msg = "Meteora DAMM v1 havuzlarını listele (v2 değil)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_not_triggered(r, "meteora_dammv2_get_pools", msg)

    def test_dlmm_sorusu_v1_tetiklememeli(self, chat_client):
        msg = "Meteora DLMM'de pozisyonlarımı göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert not any("dammv1" in t for t in triggered), (
            f"DLMM sorusunda dammv1 eylemi tetiklendi: {triggered}\n{msg}"
        )

    def test_v2_sorusu_v1_tetiklememeli(self, chat_client):
        msg = "Meteora DAMM v2 havuz gruplarını listele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert not any("dammv1" in t for t in triggered), (
            f"v2 sorusunda dammv1 eylemi tetiklendi: {triggered}\n{msg}"
        )

    def test_dynamic_amm_v1_olarak_anlasilmali(self, chat_client):
        msg = "Meteora Dynamic AMM farm'larını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)

    def test_pools_search_vs_get_pools(self, chat_client):
        msg = "Meteora DAMM v1'de BONK kelimesiyle havuz ara"
        r = _chat(chat_client, msg)
        # search_pools bekleniyor, get_pools değil
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_param_present(r, "meteora_dammv1_search_pools", "filter", "BONK", msg)

    def test_farms_vs_get_pools_farms_type(self, chat_client):
        """'farms' kelimesi kullanıcı farm endpoint'ini mi yoksa get_pools?farms tipini mi istiyor?"""
        msg = "Meteora DAMM v1 farm programlarını getir (yield farming)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)

    def test_alpha_vault_vs_pool_config(self, chat_client):
        msg = "Meteora Alpha Vault konfigürasyonlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vault_configs", msg)
        assert_not_triggered(r, "meteora_dammv1_get_pool_configs", msg)

    def test_metrics_vs_get_pools_routing(self, chat_client):
        msg = "Meteora Dynamic AMM protokol düzeyinde istatistikleri göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_not_triggered(r, "meteora_dammv1_search_pools", msg)

    def test_search_pools_vs_get_pools_adres(self, chat_client):
        msg = f"Meteora DAMM v1 havuzu {DV1_SOL_USDC}"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_dammv1_get_pools", "meteora_dammv1_search_pools"
        ]), f"Beklenen eylem tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"

    def test_v1_pool_configs_vs_fee_config(self, chat_client):
        msg = "Meteora Dynamic AMM genel havuz konfigürasyonları nelerdir?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_dammv1_get_pool_configs",
            "meteora_dammv1_get_fee_config",
        ]), f"Config eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"

    def test_farms_type_pool_listing(self, chat_client):
        msg = "Meteora Dynamic AMM'de farms tipindeki havuzları (pool_type=farms) listele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        # get_pools veya search_pools tetiklenebilir (pool_type=farms)
        assert any(t in triggered for t in [
            "meteora_dammv1_get_pools", "meteora_dammv1_search_pools"
        ]), f"Beklenen eylem tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"

    def test_alpha_vault_vs_get_vaults_farming(self, chat_client):
        msg = "Meteora Alpha Vault listesini göster (farming vault değil, token launch için)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)

    def test_dlmm_stats_vs_dammv1_metrics(self, chat_client):
        msg = "Meteora Dynamic AMM istatistiklerini göster (DLMM değil)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert not any("dlmm" in t for t in triggered), (
            f"DLMM eylemi yanlışlıkla tetiklendi: {triggered}\n{msg}"
        )

    def test_search_with_sort_not_get_pools(self, chat_client):
        msg = "Meteora DAMM v1 havuzlarını TVL'ye göre sıralı listele (ilk 10)"
        r = _chat(chat_client, msg)
        # Sıralama + liste → search_pools bekleniyor
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)

    def test_general_meteora_query_v1_context(self, chat_client):
        msg = "Meteora Dynamic AMM v1 genel durumunu göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 9. ERROR HANDLING — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1ErrorHandling:

    def test_gecersiz_havuz_adresi_get_pools(self, chat_client):
        msg = f"Meteora DAMM v1'de {GARBAGE_ADDR} adresli havuzu göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if triggered:
            assert_explains_error(r, msg)
        else:
            assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_yanlis_pool_type(self, chat_client):
        msg = "Meteora DAMM v1'de 'perpetual' tipindeki havuzları göster"
        r = _chat(chat_client, msg)
        # LLM ya clarify ister ya da geçerli bir tür ile devam eder
        assert result_has_response(r), f"LLM yanıt vermedi.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_search_pools_filter_yok_page_sorulur(self, chat_client):
        msg = "Meteora DAMM v1 havuzlarını ara"
        r = _chat(chat_client, msg)
        # filter belirtilmedi, LLM filtre sormalı ya da genel arama yapmalı
        assert result_has_response(r), f"LLM yanıt vermedi.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_fee_config_adres_eksik(self, chat_client):
        msg = "Meteora DAMM v1 fee config bilgisini göster"
        r = _chat(chat_client, msg)
        # Adres olmadan fee config yapılamaz, LLM clarify isteyebilir
        assert result_has_response(r), f"LLM yanıt vermedi.\n{msg}"

    def test_search_sort_invalid_key(self, chat_client):
        msg = "Meteora DAMM v1 havuzlarını 'popularity'ye göre sırala"
        r = _chat(chat_client, msg)
        # LLM geçerli bir sort key seçmeli veya kullanıcıya sorumlu
        triggered = r.all_triggered_types()
        if "meteora_dammv1_search_pools" in triggered:
            assert_valid_sort_key_sent(r, "meteora_dammv1_search_pools", msg)
        else:
            assert_text_not_empty(r, msg)

    def test_alpha_vault_fake_pool_address(self, chat_client):
        msg = f"Meteora Alpha Vault'unu {FAKE_ADDR} havuz adresiyle göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_search_pools_negatif_sayfa(self, chat_client):
        msg = "Meteora DAMM v1 havuzlarının -1. sayfasını göster"
        r = _chat(chat_client, msg)
        # LLM 0 veya 1'den başlamalı
        if "meteora_dammv1_search_pools" in r.all_triggered_types():
            p = r.params_for("meteora_dammv1_search_pools")
            page = int(p.get("page", 0))
            assert page >= 0, f"Negatif page gönderildi: page={page}\n{msg}"

    def test_api_hata_yumusak_yanit(self, chat_client):
        msg = f"Meteora DAMM v1 havuzu hakkında bilgi ver: {GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_anlamsiz_pool_type_filtreleme(self, chat_client):
        msg = "Meteora DAMM v1'de 'nft_pool' tipindeki havuzları listele"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM yanıt vermedi.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_search_cok_buyuk_size(self, chat_client):
        msg = "Meteora DAMM v1 havuzlarının tamamını listele, 10000 tane getir"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_dammv1_search_pools" in triggered:
            p = r.params_for("meteora_dammv1_search_pools")
            size = int(p.get("size", 0))
            # LLM mantıklı bir sınır koymalı
            assert size <= 1000, f"Aşırı büyük size gönderildi: {size}\n{msg}"

    def test_farms_type_confusion(self, chat_client):
        msg = "Meteora DAMM v1 farms endpoint'ini çağır"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_text_not_empty(r, msg)

    def test_metrics_extra_param_gonderilmemeli(self, chat_client):
        msg = "Meteora Dynamic AMM tüm havuzların metriklerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        p = r.params_for("meteora_dammv1_get_pools_metrics")
        bad = [k for k in p if p[k] is not None and k in ("poolAddresses", "pool_addresses")]
        assert not bad, f"Metrics endpoint param almaz ama gönderildi: {bad}\n{msg}"


# ══════════════════════════════════════════════════════════════════════════════
# 10. USER GUIDANCE — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1UserGuidance:

    def test_v1_vs_v2_fark_sorusu(self, chat_client):
        msg = "Meteora Dynamic AMM v1 ile v2 arasındaki fark nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["v1", "v2", "damm", "dynamic"], msg, min_hits=2)

    def test_v1_vs_dlmm_fark_sorusu(self, chat_client):
        msg = "Meteora Dynamic AMM ile DLMM arasındaki fark nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_mentions(r, ["dlmm", "dynamic", "amm", "likidite"], msg, min_hits=2)

    def test_hangi_havuza_likidite_eklemeliyim(self, chat_client):
        msg = "Meteora Dynamic AMM'de SOL-USDC havuzuna likidite eklemek istiyorum, nasıl yapayım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_pool_secimi_icin_yonlendirme(self, chat_client):
        msg = "Meteora Dynamic AMM'de en iyi havuzu nasıl seçerim?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["tvl", "apr", "fee", "likidite", "havuz", "pool"], msg)

    def test_alpha_vault_nasil_kullanilir(self, chat_client):
        msg = "Meteora Alpha Vault nasıl kullanılır? Token launch için ne yapmalıyım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_farming_stratejisi_onerileri(self, chat_client):
        msg = "Meteora Dynamic AMM'de farming stratejileri nelerdir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_lst_havuz_mantigi(self, chat_client):
        msg = "Meteora Dynamic AMM LST havuzları ne işe yarar?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_mentions(r, ["lst", "liquid", "staking", "stake", "sol"], msg)

    def test_clarification_belirsiz_sorgu(self, chat_client):
        msg = "Meteora'da havuz nerede?"
        r = _chat(chat_client, msg)
        # LLM ya clarify ister ya da genel bilgi verir
        assert result_has_response(r), f"LLM yanıt vermedi.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_tvl_yorum_ve_oneri(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını getir ve TVL'ye göre en iyi 3'ünü öner"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_fee_yapisi_anlat(self, chat_client):
        msg = "Meteora Dynamic AMM'de ücret yapısı nasıl çalışıyor?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_mentions(r, ["fee", "ücret", "komisyon", "bps", "%"], msg)

    def test_multitoken_havuz_ne_demek(self, chat_client):
        msg = "Meteora Dynamic AMM'de multitoken havuz nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_kullanici_hizli_baslangic_rehberi(self, chat_client):
        msg = "Meteora Dynamic AMM'e yeni başlayan biri olarak neye bakmalıyım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 11. INTERPRETATION DEPTH — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestDV1InterpretationDepth:

    def test_lst_havuz_yorumu_anlamli(self, chat_client):
        msg = "Meteora Dynamic AMM LST havuzlarını listele ve en iyi APR'liyi açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_search_sonucu_yorum_derinligi(self, chat_client):
        msg = "Meteora DAMM v1'de SOL içeren havuzları ara ve en büyüğü için TVL, APR ve hacmini açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_search_pools", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["tvl", "apr", "volume", "hacim"], msg, min_hits=2)

    def test_metrics_analiz_derinligi(self, chat_client):
        msg = "Meteora Dynamic AMM'in genel sağlığını değerlendir: TVL, hacim, havuz sayısı"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pools_metrics", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_farm_yorumu_anlamli(self, chat_client):
        msg = "Meteora Dynamic AMM farm programlarını getir ve hangi ödüllerin mevcut olduğunu açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_farms", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_alpha_vault_derinlik_yorumu(self, chat_client):
        msg = "Meteora Alpha Vault'larını getir ve nasıl kullanacağımı adım adım açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_alpha_vaults", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_havuz_karsilastirmasi(self, chat_client):
        msg = "Meteora DAMM v1'de SOL-USDC ve SOL-USDT havuzlarını karşılaştır"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_tvl_lider_havuz_analiz(self, chat_client):
        msg = "Meteora Dynamic AMM'de en yüksek TVL'li 5 havuzu bul ve kısaca analiz et"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_pool_config_anlam_cikartma(self, chat_client):
        msg = "Meteora Dynamic AMM pool config presetlerini getir ve hangi fee tier'ın ne zaman kullanılacağını açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dammv1_get_pool_configs", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_genel_durum_ozeti(self, chat_client):
        msg = "Meteora Dynamic AMM'in bugünkü genel durumunu özet halinde ver"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_does_not_hallucinate_data(r, msg)

    def test_yatirim_karari_icin_veri(self, chat_client):
        msg = "Meteora Dynamic AMM'e likidite sağlamak istiyorum. Hangi havuz en iyi APR+TVL dengesine sahip?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_monitoring_havuz_onemi(self, chat_client):
        msg = "Meteora DAMM v1 izlenen havuzları neden önemli? Onları listele ve açıkla"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("dammv1" in t for t in triggered), (
            f"dammv1 eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_havuz_secim_kriterleri(self, chat_client):
        msg = "Meteora Dynamic AMM havuzları arasından düşük riskli birini seçmek için hangi kriterlere bakmalıyım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["tvl", "apr", "fee", "risk", "havuz", "pool"], msg, min_hits=2)

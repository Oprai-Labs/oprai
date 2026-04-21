"""
Meteora — Kapsamlı LLM Kalite Testi (Part B: DAMM v1 + S2E + Dynamic Vault)

Her endpoint için 5-10 farklı senaryo:
  - Yeni token kombinasyonları (önceki testlerden farklı)
  - Birden fazla parametre birlikte
  - Yanıt yorumlama derinliği
  - Hata senaryoları ve edge case'ler
  - Türkçe + İngilizce karma
  - Belirsizlik, clarification gerektiren durumlar

Test sınıfları (Part B):
  TestDV1GetPools_Advanced       — 8 test
  TestDV1SearchPools_Advanced    — 8 test
  TestDV1GetFarms_Advanced       — 6 test
  TestDV1GetPoolsMetrics_Adv     — 6 test
  TestDV1GetAlphaVaults_Advanced — 8 test
  TestDV1GetAlphaVaultConfigs_Adv— 6 test
  TestDV1GetPoolConfigs_Advanced — 6 test
  TestDV1GetFeeConfig_Advanced   — 7 test
  TestS2EGetAnalytics_Advanced   — 7 test
  TestS2EGetAllVaults_Advanced   — 7 test
  TestS2EFilterVaults_Advanced   — 9 test
  TestS2EGetVault_Advanced       — 8 test
  TestVaultGetInfo_Advanced      — 8 test
  TestVaultGetAddresses_Advanced — 7 test
  TestVaultGetState_Advanced     — 8 test
  TestVaultGetApy_Advanced       — 8 test
  TestVaultGetApyHistory_Advanced— 9 test
  TestVaultGetVirtualPrice_Adv   — 8 test

Toplam Part B: ~132 test
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid

import httpx
import pytest

# ─── Config ───────────────────────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://localhost:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

# DAMM v1 havuzları
DV1_USDC_USDT  = "2QdhepnKRTLjjSqPL1PtKNwqrUkoLee5Gqs8bvZhRdAv"
DV1_MSOL_SOL   = "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP"
DV1_JTO_USDC   = "6UmmUiYoBjSrhakAobJw8BYkjzKyVdwPko8mmspnaeFV"
DV1_PYTH_USDC  = "5BDjpUBmHhT2RhWDnL2hF3Gg3PKy9vu3EraF5nzXvN1A"

# Stake2Earn vault adresleri
S2E_BONK_VAULT  = "7NzRBFCBgN7YxSbTCroWBMdJxMJH8eDRdMvs2ajRKajh"
S2E_WIF_VAULT   = "FfzNJeG2eMhJfHjrZHbBRoSLSCVJU3MgFo3YXhKRmS5B"
S2E_JUP_VAULT   = "9gfzb4h9y4U8k2nBwMuvpGCFGW4CYrWkJPmtGqHDQ3Lm"
S2E_JTO_VAULT   = "4XKnvFXb7YQfLJHZNNr3Yp5BXCnN9mQ3DhMGF8qjX6Kp"

# Dynamic Vault mints
SOL_MINT    = "So11111111111111111111111111111111111111112"
USDC_MINT   = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT   = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MSOL_MINT   = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
JITOOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
BONK_MINT   = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
PYTH_MINT   = "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"
JUP_MINT    = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
JTO_MINT    = "jtojtomepa8bduh8b5JsfX6LB6BH1EaD7SyC6vDKpLc"

# Dynamic Vault strategy adresleri
USDC_STRATEGY_MARGINFI = "2bnZ1edbvK3CjRvNTgkaSUTBSXb5jXNT7GFVEnWkbsHZ"
SOL_STRATEGY_JITO      = "4fmahDpFQdQEr9DnfJzjHXYVUxWPGWkFcQmNxWfDUTMQ"

FAKE_ADDR = "FakeAddr111111111111111111111111111111111111"

# Yaklaşık timestamp hesaplamaları
NOW_TS      = int(time.time())
LAST_7_DAYS = NOW_TS - (7 * 86400)
LAST_30_DAYS = NOW_TS - (30 * 86400)
LAST_90_DAYS = NOW_TS - (90 * 86400)


# ─── Fixture ──────────────────────────────────────────────────────────────────

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


# ─── Yardımcılar ─────────────────────────────────────────────────────────────

def assert_triggered(r: StreamResult, t: str, msg: str = ""):
    assert t in r.all_triggered_types(), (
        f"'{t}' tetiklenmedi. Tetiklenenler: {r.all_triggered_types()}\n"
        f"LLM: {r.text[:400]}\n{msg}"
    )


def assert_not_triggered(r: StreamResult, t: str, msg: str = ""):
    assert t not in r.all_triggered_types(), (
        f"İstenmeyen '{t}' tetiklendi.\nLLM: {r.text[:300]}\n{msg}"
    )


def assert_param(r: StreamResult, action: str, key: str, contains: str | None = None, msg: str = ""):
    for item in r.actions + r.queries:
        if item.get("type") == action:
            p = item.get("params", {})
            assert key in p, f"[{action}] '{key}' eksik. Params: {p}\n{msg}"
            if contains:
                assert contains.lower() in str(p[key]).lower(), (
                    f"[{action}] '{key}'={p[key]!r} içinde '{contains}' yok\n{msg}"
                )
            return
    pytest.fail(f"[{action}] hiç tetiklenmedi. Tetiklenenler: {r.all_triggered_types()}\n{msg}")


def assert_param_absent(r: StreamResult, action: str, key: str, msg: str = ""):
    p = r.params_for(action)
    assert key not in p or p[key] is None, (
        f"[{action}] '{key}' gönderilmemeli: {p[key]!r}\n{msg}"
    )


def assert_no_raw_json(r: StreamResult, msg: str = ""):
    t = r.text
    assert not (t.count("{") + t.count("}") > 10 and t.count('"') > 20), (
        f"LLM ham JSON döktü.\n{t[:500]}\n{msg}"
    )


def assert_has_number(r: StreamResult, msg: str = ""):
    assert re.search(r"\d[\d,.]*", r.text), (
        f"LLM yanıtında sayı yok.\n{r.text[:400]}\n{msg}"
    )


def assert_mentions(r: StreamResult, keywords: list[str], min_hits: int = 1, msg: str = ""):
    lower = r.text_lower()
    hits = [kw for kw in keywords if kw.lower() in lower]
    assert len(hits) >= min_hits, (
        f"LLM şunlardan hiçbirini içermiyor: {keywords}\nMetin: {r.text[:400]}\n{msg}"
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
        assert p not in lower, f"Halusinasyon: '{p}'\nMetin: {r.text[:400]}\n{msg}"


def assert_explains_error(r: StreamResult, msg: str = ""):
    kws = ["hata", "geçersiz", "bulunamadı", "error", "invalid", "not found", "sorry", "üzgün"]
    lower = r.text_lower()
    assert any(k in lower for k in kws) or r.asked_clarification(), (
        f"Hata için açıklama yok.\nMetin: {r.text[:400]}\n{msg}"
    )


def assert_timestamp_is_reasonable(p: dict, key: str, msg: str = ""):
    if key not in p:
        return
    ts = int(p[key])
    min_ts = 1577836800  # 2020-01-01
    max_ts = 2051222400  # 2035-01-01
    assert min_ts < ts < max_ts, (
        f"'{key}'={ts} makul Unix timestamp aralığında değil (2020-2035)\n{msg}"
    )


def assert_no_wrong_filter_param(r: StreamResult, action: str, wrong_keys: list[str], msg: str = ""):
    p = r.params_for(action)
    for k in wrong_keys:
        assert k not in p or p[k] is None, (
            f"[{action}] '{k}' gönderilmemeli (geçersiz param): {p[k]!r}\n{msg}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — get_pools (Advanced scenarios)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1GetPools_Advanced:
    """DAMM v1 get_pools: 8 yeni senaryo"""

    def test_multitoken_pool_type(self, chat_client):
        r = _chat(chat_client, "DAMM v1'de multitoken havuzlarını listele")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        p = r.params_for("DAMMV1_GET_POOLS")
        pool_type = str(p.get("poolType", "")).lower()
        assert "multitoken" in pool_type or pool_type == "", (
            f"poolType 'multitoken' olmalı: {pool_type}"
        )

    def test_lst_pool_type(self, chat_client):
        r = _chat(chat_client, "Show LST liquidity pools on Meteora DAMM v1")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        p = r.params_for("DAMMV1_GET_POOLS")
        pool_type = str(p.get("poolType", "")).lower()
        assert "lst" in pool_type or pool_type == "", (
            f"poolType 'lst' olmalı: {pool_type}"
        )

    def test_hide_low_tvl_filter(self, chat_client):
        r = _chat(chat_client, "DAMM v1 havuzlarını göster ama düşük TVL olanları gizle")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        p = r.params_for("DAMMV1_GET_POOLS")
        assert p.get("hideLowTvl") is True or str(p.get("hideLowTvl", "")).lower() == "true", (
            f"hideLowTvl=true bekleniyor. Params: {p}"
        )

    def test_hide_low_apr_filter(self, chat_client):
        r = _chat(chat_client, "List DAMM v1 pools but filter out low APR ones")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        p = r.params_for("DAMMV1_GET_POOLS")
        assert p.get("hideLowApr") is True or str(p.get("hideLowApr", "")).lower() == "true", (
            f"hideLowApr=true bekleniyor. Params: {p}"
        )

    def test_monitoring_filter(self, chat_client):
        r = _chat(chat_client, "Sadece izlenen (monitored) DAMM v1 havuzlarını göster")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        p = r.params_for("DAMMV1_GET_POOLS")
        assert p.get("isMonitoring") is True or str(p.get("isMonitoring", "")).lower() == "true", (
            f"isMonitoring=true bekleniyor. Params: {p}"
        )

    def test_combined_type_and_hide_low_tvl(self, chat_client):
        r = _chat(chat_client, "Show DAMM v1 dynamic pools with decent TVL only")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        p = r.params_for("DAMMV1_GET_POOLS")
        assert "dynamic" in str(p.get("poolType", "")).lower() or p.get("hideLowTvl"), (
            f"poolType veya hideLowTvl bekleniyor. Params: {p}"
        )

    def test_msol_sol_pool_filter(self, chat_client):
        r = _chat(chat_client, f"DAMM v1'de mSOL içeren havuzları listele")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_response_includes_pool_summary(self, chat_client):
        r = _chat(chat_client, "DAMM v1 stabil coin havuzlarını (USDC-USDT) getir ve özetle")
        assert_triggered(r, "DAMMV1_GET_POOLS")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — search_pools (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1SearchPools_Advanced:
    """DAMM v1 search_pools: 8 yeni senaryo"""

    def test_search_with_sortkey_tvl(self, chat_client):
        r = _chat(chat_client, "DAMM v1'de SOL kelimesi ile ara ve TVL'ye göre sırala")
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        p = r.params_for("DAMMV1_SEARCH_POOLS")
        assert "sortKey" in p or "sort_key" in p, f"sortKey parametresi eksik. Params: {p}"
        sort_val = str(p.get("sortKey", p.get("sort_key", ""))).lower()
        assert "tvl" in sort_val or sort_val == "", f"sortKey 'tvl' bekleniyor: {sort_val}"

    def test_search_with_volume_sort_desc(self, chat_client):
        r = _chat(chat_client, "Search DAMM v1 pools for 'USDC', sort by volume descending")
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        p = r.params_for("DAMMV1_SEARCH_POOLS")
        order = str(p.get("orderBy", p.get("order_by", ""))).lower()
        assert "desc" in order or order == "", f"orderBy 'desc' bekleniyor: {order}"

    def test_search_page_and_size(self, chat_client):
        r = _chat(chat_client, "DAMM v1'de 'BONK' ara, sayfa 2, her sayfada 10 sonuç")
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        p = r.params_for("DAMMV1_SEARCH_POOLS")
        page = int(p.get("page", 0))
        size = int(p.get("pageSize", p.get("size", 0)))
        assert page >= 2, f"page >= 2 bekleniyor. Params: {p}"
        assert size == 10, f"pageSize=10 bekleniyor. Params: {p}"

    def test_search_pyth_fee_ratio_sort(self, chat_client):
        r = _chat(chat_client, "Search DAMM v1 for PYTH pools, sort by fee/TVL ratio")
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        p = r.params_for("DAMMV1_SEARCH_POOLS")
        sort_val = str(p.get("sortKey", "")).lower()
        assert "fee" in sort_val or "tvl" in sort_val or "ratio" in sort_val or sort_val == "", (
            f"fee_tvl_ratio sort bekleniyor: {sort_val}"
        )

    def test_search_jto_asc_order(self, chat_client):
        r = _chat(chat_client, "DAMM v1'de JTO ara, en düşük TVL'den başlayarak listele")
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        p = r.params_for("DAMMV1_SEARCH_POOLS")
        order = str(p.get("orderBy", p.get("order_by", ""))).lower()
        assert "asc" in order or order == "", f"orderBy 'asc' bekleniyor: {order}"

    def test_search_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, "Search for MSOL pools in DAMM v1 and tell me which is best")
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_search_requires_page_and_size(self, chat_client):
        r = _chat(chat_client, "Find DAMM v1 pools matching 'stable'")
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        p = r.params_for("DAMMV1_SEARCH_POOLS")
        has_page = "page" in p
        has_size = "pageSize" in p or "size" in p or "limit" in p
        assert has_page and has_size, (
            f"search_pools her zaman page+size gerektiriyor. Params: {p}"
        )

    def test_search_combined_all_params(self, chat_client):
        r = _chat(chat_client, (
            "DAMM v1'de 'SOL' için arama yap, "
            "TVL'ye göre azalan sırayla sırala, "
            "sayfa 1, 15 sonuç"
        ))
        assert_triggered(r, "DAMMV1_SEARCH_POOLS")
        p = r.params_for("DAMMV1_SEARCH_POOLS")
        assert "page" in p, f"page eksik. Params: {p}"
        assert int(p.get("pageSize", p.get("size", 0))) == 15, (
            f"pageSize=15 bekleniyor. Params: {p}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — get_farms (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1GetFarms_Advanced:
    """DAMM v1 get_farms: 6 yeni senaryo"""

    def test_farms_no_params(self, chat_client):
        r = _chat(chat_client, "List all Meteora DAMM v1 farms")
        assert_triggered(r, "DAMMV1_GET_FARMS")
        p = r.params_for("DAMMV1_GET_FARMS")
        assert len([v for v in p.values() if v is not None]) == 0, (
            f"get_farms parametresiz çağrılmalı. Params: {p}"
        )

    def test_farms_response_mentions_rewards(self, chat_client):
        r = _chat(chat_client, "DAMM v1 farm ödüllerini göster")
        assert_triggered(r, "DAMMV1_GET_FARMS")
        assert_no_raw_json(r)
        assert_mentions(r, ["farm", "reward", "ödül", "apr", "stake"], min_hits=1)

    def test_farms_not_pool_specific(self, chat_client):
        r = _chat(chat_client, f"DAMM v1 farmlarını getir (tüm farmlar)")
        assert_triggered(r, "DAMMV1_GET_FARMS")
        p = r.params_for("DAMMV1_GET_FARMS")
        assert_param_absent(r, "DAMMV1_GET_FARMS", "poolAddress")

    def test_farms_quality_response(self, chat_client):
        r = _chat(chat_client, "Which DAMM v1 farm has the highest APR right now?")
        assert_triggered(r, "DAMMV1_GET_FARMS")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_no_hallucination(r)

    def test_farms_vs_pools_routing(self, chat_client):
        r = _chat(chat_client, "What's available to farm on Meteora DAMM v1?")
        assert_triggered(r, "DAMMV1_GET_FARMS")
        assert_not_triggered(r, "DAMMV1_GET_POOLS")
        assert_no_raw_json(r)

    def test_farms_turkish_query(self, chat_client):
        r = _chat(chat_client, "Meteora DAMM v1 farm listesini göster, ödül tokenlerini belirt")
        assert_triggered(r, "DAMMV1_GET_FARMS")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — get_pools_metrics (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1GetPoolsMetrics_Advanced:
    """DAMM v1 get_pools_metrics: 6 yeni senaryo"""

    def test_aggregate_metrics_no_params(self, chat_client):
        r = _chat(chat_client, "Get aggregate pool metrics for Meteora DAMM v1")
        assert_triggered(r, "DAMMV1_GET_POOLS_METRICS")
        p = r.params_for("DAMMV1_GET_POOLS_METRICS")
        assert len([v for v in p.values() if v is not None]) == 0, (
            f"get_pools_metrics param almamalı. Params: {p}"
        )

    def test_metrics_tvl_question(self, chat_client):
        r = _chat(chat_client, "DAMM v1 protokolünün toplam TVL metrikleri")
        assert_triggered(r, "DAMMV1_GET_POOLS_METRICS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_metrics_volume_24h(self, chat_client):
        r = _chat(chat_client, "What is the 24h trading volume across all DAMM v1 pools?")
        assert_triggered(r, "DAMMV1_GET_POOLS_METRICS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_metrics_fee_collected(self, chat_client):
        r = _chat(chat_client, "DAMM v1'de bugün toplanan toplam fee miktarı")
        assert_triggered(r, "DAMMV1_GET_POOLS_METRICS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_metrics_no_pool_specific_param(self, chat_client):
        r = _chat(chat_client, f"DAMM v1 genel metrikleri — tüm havuzlar için toplam")
        assert_triggered(r, "DAMMV1_GET_POOLS_METRICS")
        assert_param_absent(r, "DAMMV1_GET_POOLS_METRICS", "pool_addresses")
        assert_param_absent(r, "DAMMV1_GET_POOLS_METRICS", "poolAddress")

    def test_metrics_response_quality(self, chat_client):
        r = _chat(chat_client, "Summarize DAMM v1 health metrics — TVL, volume, fees")
        assert_triggered(r, "DAMMV1_GET_POOLS_METRICS")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — get_alpha_vaults (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1GetAlphaVaults_Advanced:
    """DAMM v1 get_alpha_vaults: 8 yeni senaryo"""

    def test_all_alpha_vaults_no_filter(self, chat_client):
        r = _chat(chat_client, "List all Meteora alpha vaults")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULTS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_filter_by_pool_address(self, chat_client):
        r = _chat(chat_client, f"Alpha vaults for pool {DV1_JTO_USDC}")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULTS")
        assert_param(r, "DAMMV1_GET_ALPHA_VAULTS", "poolAddress", contains=DV1_JTO_USDC)

    def test_filter_by_base_mint_sol(self, chat_client):
        r = _chat(chat_client, f"Show alpha vaults where base token is SOL (mint: {SOL_MINT})")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULTS")
        p = r.params_for("DAMMV1_GET_ALPHA_VAULTS")
        assert SOL_MINT in str(p), f"SOL mint adresi gönderilmedi. Params: {p}"

    def test_filter_by_base_mint_usdc(self, chat_client):
        r = _chat(chat_client, f"Alpha vaults with USDC as base token: {USDC_MINT}")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULTS")
        p = r.params_for("DAMMV1_GET_ALPHA_VAULTS")
        assert USDC_MINT in str(p), f"USDC mint adresi gönderilmedi. Params: {p}"

    def test_what_is_alpha_vault_response(self, chat_client):
        r = _chat(chat_client, "What are Meteora alpha vaults and how do they work?")
        # LLM might explain and then trigger, or just explain
        assert r.has_text_content(), "Alpha vault hakkında açıklama bekleniyor"
        assert_no_raw_json(r)

    def test_alpha_vault_tvl_query(self, chat_client):
        r = _chat(chat_client, "En yüksek TVL'li Meteora alpha vault hangisi?")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULTS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_combined_pool_and_base_mint(self, chat_client):
        r = _chat(chat_client, (
            f"Find alpha vaults for pool {DV1_USDC_USDT} "
            f"where base token is USDC ({USDC_MINT})"
        ))
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULTS")
        p = r.params_for("DAMMV1_GET_ALPHA_VAULTS")
        assert DV1_USDC_USDT in str(p) or USDC_MINT in str(p), (
            f"Pool veya baseMint parametresi eksik. Params: {p}"
        )

    def test_alpha_vault_response_no_hallucination(self, chat_client):
        r = _chat(chat_client, "Meteora alpha vault detaylarını getir ve analiz et")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULTS")
        assert_no_hallucination(r)
        assert_no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — get_alpha_vault_configs (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1GetAlphaVaultConfigs_Advanced:
    """DAMM v1 get_alpha_vault_configs: 6 yeni senaryo"""

    def test_list_all_configs(self, chat_client):
        r = _chat(chat_client, "List all alpha vault configurations on Meteora")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        assert_no_raw_json(r)

    def test_configs_no_params(self, chat_client):
        r = _chat(chat_client, "Get alpha vault config options")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        p = r.params_for("DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        assert len([v for v in p.values() if v is not None]) == 0, (
            f"Alpha vault configs parametresiz olmalı. Params: {p}"
        )

    def test_configs_for_creating_vault(self, chat_client):
        r = _chat(chat_client, "Yeni bir alpha vault oluşturmak için hangi config seçenekleri var?")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_configs_response_mentions_parameters(self, chat_client):
        r = _chat(chat_client, "What parameters can I configure for a Meteora alpha vault?")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_configs_not_triggered_for_state_query(self, chat_client):
        r = _chat(chat_client, "Show available alpha vault config templates")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        assert_no_raw_json(r)

    def test_configs_response_quality(self, chat_client):
        r = _chat(chat_client, "Meteora alpha vault konfigürasyon şablonlarını açıkla")
        assert_triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — get_pool_configs (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1GetPoolConfigs_Advanced:
    """DAMM v1 get_pool_configs: 6 yeni senaryo"""

    def test_list_pool_configs(self, chat_client):
        r = _chat(chat_client, "Show me all available pool configs for Meteora DAMM v1")
        assert_triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        assert_no_raw_json(r)

    def test_pool_configs_no_params(self, chat_client):
        r = _chat(chat_client, "Get DAMM v1 pool configuration options")
        assert_triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        p = r.params_for("DAMMV1_GET_POOL_CONFIGS")
        assert len([v for v in p.values() if v is not None]) == 0, (
            f"get_pool_configs parametresiz olmalı. Params: {p}"
        )

    def test_fee_tiers_available(self, chat_client):
        r = _chat(chat_client, "What fee tiers are available when creating a DAMM v1 pool?")
        assert_triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        assert_no_raw_json(r)
        assert_mentions(r, ["fee", "tier", "ücret", "%", "bps", "basis"], min_hits=1)

    def test_curve_types_question(self, chat_client):
        r = _chat(chat_client, "DAMM v1 hangi eğri (curve) tiplerini destekliyor?")
        assert_triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        assert_no_raw_json(r)

    def test_pool_configs_for_new_pool(self, chat_client):
        r = _chat(chat_client, "I want to create a new DAMM v1 pool. What configs are available?")
        assert_triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_configs_response_actionable(self, chat_client):
        r = _chat(chat_client, "DAMM v1 pool kurulum seçeneklerini açıkla, hangi fee tier iyi?")
        assert_triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMM v1 — get_fee_config (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1GetFeeConfig_Advanced:
    """DAMM v1 get_fee_config: 7 yeni senaryo"""

    def test_fee_config_by_pool_address(self, chat_client):
        r = _chat(chat_client, f"Get fee configuration for DAMM v1 pool {DV1_USDC_USDT}")
        assert_triggered(r, "DAMMV1_GET_FEE_CONFIG")
        assert_param(r, "DAMMV1_GET_FEE_CONFIG", "poolAddress", contains=DV1_USDC_USDT)

    def test_fee_config_msol_sol(self, chat_client):
        r = _chat(chat_client, f"mSOL-SOL havuzunun fee yapısı nedir? {DV1_MSOL_SOL}")
        assert_triggered(r, "DAMMV1_GET_FEE_CONFIG")
        assert_param(r, "DAMMV1_GET_FEE_CONFIG", "poolAddress", contains=DV1_MSOL_SOL)
        assert_no_raw_json(r)
        assert_mentions(r, ["fee", "ücret", "%", "bps", "komisyon"], min_hits=1)

    def test_fee_config_jto_pool(self, chat_client):
        r = _chat(chat_client, f"What's the fee structure for pool {DV1_JTO_USDC}?")
        assert_triggered(r, "DAMMV1_GET_FEE_CONFIG")
        assert_param(r, "DAMMV1_GET_FEE_CONFIG", "poolAddress", contains=DV1_JTO_USDC)

    def test_fee_config_no_address_handled(self, chat_client):
        r = _chat(chat_client, "DAMM v1 fee config nasıl?")
        # LLM ya soru sormalı ya da genel bir cevap vermeli
        assert r.asked_clarification() or r.has_text_content(), (
            "Adres eksikken LLM tepki vermedi"
        )

    def test_fee_config_response_includes_percentages(self, chat_client):
        r = _chat(chat_client, f"Show the exact fee rates for pool {DV1_PYTH_USDC}")
        assert_triggered(r, "DAMMV1_GET_FEE_CONFIG")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_fee_config_dynamic_vs_fixed(self, chat_client):
        r = _chat(chat_client, (
            f"Is the fee in pool {DV1_USDC_USDT} dynamic or fixed? "
            "What's the current rate?"
        ))
        assert_triggered(r, "DAMMV1_GET_FEE_CONFIG")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_fee_config_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            f"I'm comparing fee costs across pools. "
            f"What's the fee config for {DV1_JTO_USDC}?"
        ))
        assert_triggered(r, "DAMMV1_GET_FEE_CONFIG")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# Stake2Earn — get_analytics (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestS2EGetAnalytics_Advanced:
    """S2E get_analytics: 7 yeni senaryo"""

    def test_s2e_overall_analytics(self, chat_client):
        r = _chat(chat_client, "Show me Meteora Stake2Earn analytics")
        assert_triggered(r, "S2E_GET_ANALYTICS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_s2e_tvl_total(self, chat_client):
        r = _chat(chat_client, "Stake2Earn'in toplam kilitli değeri (TVL) nedir?")
        assert_triggered(r, "S2E_GET_ANALYTICS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_s2e_yield_rates(self, chat_client):
        r = _chat(chat_client, "What are the current yield rates on Meteora S2E?")
        assert_triggered(r, "S2E_GET_ANALYTICS")
        assert_no_raw_json(r)
        assert_mentions(r, ["yield", "apr", "apy", "getiri", "%"], min_hits=1)

    def test_s2e_total_staked(self, chat_client):
        r = _chat(chat_client, "How much total SOL is staked across all S2E vaults?")
        assert_triggered(r, "S2E_GET_ANALYTICS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_s2e_protocol_level_query(self, chat_client):
        r = _chat(chat_client, "Meteora Stake2Earn protokolünün genel durumu nasıl?")
        assert_triggered(r, "S2E_GET_ANALYTICS")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_s2e_analytics_no_params(self, chat_client):
        r = _chat(chat_client, "Get S2E protocol analytics data")
        assert_triggered(r, "S2E_GET_ANALYTICS")
        p = r.params_for("S2E_GET_ANALYTICS")
        assert len([v for v in p.values() if v is not None]) == 0, (
            f"get_analytics parametresiz olmalı. Params: {p}"
        )

    def test_s2e_analytics_response_quality(self, chat_client):
        r = _chat(chat_client, "Stake2Earn'in performance metrikleri nelerdir? Özetle.")
        assert_triggered(r, "S2E_GET_ANALYTICS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# Stake2Earn — get_all_vaults (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestS2EGetAllVaults_Advanced:
    """S2E get_all_vaults: 7 yeni senaryo"""

    def test_all_vaults_list(self, chat_client):
        r = _chat(chat_client, "List all Meteora Stake2Earn vaults")
        assert_triggered(r, "S2E_GET_ALL_VAULTS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_all_vaults_no_params(self, chat_client):
        r = _chat(chat_client, "Show me all S2E vaults")
        assert_triggered(r, "S2E_GET_ALL_VAULTS")
        p = r.params_for("S2E_GET_ALL_VAULTS")
        assert len([v for v in p.values() if v is not None]) == 0, (
            f"get_all_vaults parametresiz olmalı. Params: {p}"
        )

    def test_all_vaults_token_listing(self, chat_client):
        r = _chat(chat_client, "Stake2Earn'de hangi tokenlar için vault var?")
        assert_triggered(r, "S2E_GET_ALL_VAULTS")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_all_vaults_best_apr(self, chat_client):
        r = _chat(chat_client, "Which S2E vault has the best APR right now?")
        assert_triggered(r, "S2E_GET_ALL_VAULTS")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_no_hallucination(r)

    def test_all_vaults_vs_filter_routing(self, chat_client):
        r = _chat(chat_client, "Tüm S2E vault'larını göster (filtresiz)")
        assert_triggered(r, "S2E_GET_ALL_VAULTS")
        assert_not_triggered(r, "S2E_FILTER_VAULTS")

    def test_all_vaults_response_summary(self, chat_client):
        r = _chat(chat_client, "S2E vault'larını özetle: hangi tokenlar, ne kadar TVL")
        assert_triggered(r, "S2E_GET_ALL_VAULTS")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_all_vaults_staking_decision(self, chat_client):
        r = _chat(chat_client, (
            "I want to stake tokens in S2E. "
            "Show me all available vaults so I can choose."
        ))
        assert_triggered(r, "S2E_GET_ALL_VAULTS")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# Stake2Earn — filter_vaults (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestS2EFilterVaults_Advanced:
    """S2E filter_vaults: 9 yeni senaryo"""

    def test_filter_single_pool_address(self, chat_client):
        r = _chat(chat_client, f"Show S2E vaults for pool {S2E_BONK_VAULT}")
        assert_triggered(r, "S2E_FILTER_VAULTS")
        assert_param(r, "S2E_FILTER_VAULTS", "poolAddress", contains=S2E_BONK_VAULT)

    def test_filter_multiple_addresses(self, chat_client):
        r = _chat(chat_client, (
            f"Filter S2E vaults for these pools: "
            f"{S2E_BONK_VAULT} and {S2E_WIF_VAULT}"
        ))
        assert_triggered(r, "S2E_FILTER_VAULTS")
        p = r.params_for("S2E_FILTER_VAULTS")
        addr_str = str(p.get("poolAddress", ""))
        assert S2E_BONK_VAULT in addr_str or S2E_WIF_VAULT in addr_str, (
            f"Adresler gönderilmedi. Params: {p}"
        )

    def test_filter_no_wrong_params(self, chat_client):
        r = _chat(chat_client, f"Filter S2E vaults by pool address {S2E_JUP_VAULT}")
        assert_triggered(r, "S2E_FILTER_VAULTS")
        assert_no_wrong_filter_param(
            r, "S2E_FILTER_VAULTS",
            ["searchTerm", "sortKey", "orderBy", "limit", "offset", "page"],
            "filter_vaults yalnızca poolAddress kabul eder"
        )

    def test_filter_three_addresses(self, chat_client):
        r = _chat(chat_client, (
            f"S2E vault filtrele: {S2E_BONK_VAULT}, {S2E_WIF_VAULT}, {S2E_JTO_VAULT}"
        ))
        assert_triggered(r, "S2E_FILTER_VAULTS")
        p = r.params_for("S2E_FILTER_VAULTS")
        addr_str = str(p.get("poolAddress", ""))
        found = sum([
            S2E_BONK_VAULT in addr_str,
            S2E_WIF_VAULT in addr_str,
            S2E_JTO_VAULT in addr_str,
        ])
        assert found >= 1, f"En az 1 adres gönderilmeli. Params: {p}"

    def test_filter_not_using_searchterm(self, chat_client):
        r = _chat(chat_client, f"Filter S2E vaults matching pool {S2E_JTO_VAULT}")
        assert_triggered(r, "S2E_FILTER_VAULTS")
        p = r.params_for("S2E_FILTER_VAULTS")
        assert "searchTerm" not in p, f"searchTerm kullanılmamalı. Params: {p}"
        assert "search_term" not in p, f"search_term kullanılmamalı. Params: {p}"

    def test_filter_no_sort_key(self, chat_client):
        r = _chat(chat_client, f"Vault filter: pool address {S2E_BONK_VAULT}")
        assert_triggered(r, "S2E_FILTER_VAULTS")
        p = r.params_for("S2E_FILTER_VAULTS")
        assert "sortKey" not in p, f"sortKey kullanılmamalı. Params: {p}"

    def test_filter_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"S2E vault'larını filtrele, pool: {S2E_WIF_VAULT}")
        assert_triggered(r, "S2E_FILTER_VAULTS")
        assert_no_raw_json(r)

    def test_filter_vs_all_vaults_routing(self, chat_client):
        r = _chat(chat_client, f"Sadece şu havuzun vault'larını göster: {S2E_JUP_VAULT}")
        assert_triggered(r, "S2E_FILTER_VAULTS")
        assert_not_triggered(r, "S2E_GET_ALL_VAULTS")

    def test_filter_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            f"Filter S2E vaults for pool {S2E_BONK_VAULT}. "
            "Tell me the APR and staked amount."
        ))
        assert_triggered(r, "S2E_FILTER_VAULTS")
        assert_no_raw_json(r)
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# Stake2Earn — get_vault (single vault)
# ═══════════════════════════════════════════════════════════════════════════════

class TestS2EGetVault_Advanced:
    """S2E get_vault: 8 yeni senaryo"""

    def test_get_vault_by_address(self, chat_client):
        r = _chat(chat_client, f"Show details for S2E vault {S2E_BONK_VAULT}")
        assert_triggered(r, "S2E_GET_VAULT")
        assert_param(r, "S2E_GET_VAULT", "vaultAddress", contains=S2E_BONK_VAULT)

    def test_get_vault_wif(self, chat_client):
        r = _chat(chat_client, f"WIF S2E vault bilgilerini göster: {S2E_WIF_VAULT}")
        assert_triggered(r, "S2E_GET_VAULT")
        assert_param(r, "S2E_GET_VAULT", "vaultAddress", contains=S2E_WIF_VAULT)

    def test_vault_apr_query(self, chat_client):
        r = _chat(chat_client, f"What APR is this S2E vault offering? {S2E_JUP_VAULT}")
        assert_triggered(r, "S2E_GET_VAULT")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_mentions(r, ["apr", "apy", "yield", "getiri", "%"], min_hits=1)

    def test_vault_staking_cap(self, chat_client):
        r = _chat(chat_client, f"Bu S2E vault'un staking limiti nedir? {S2E_JTO_VAULT}")
        assert_triggered(r, "S2E_GET_VAULT")
        assert_no_raw_json(r)

    def test_vault_unstake_period(self, chat_client):
        r = _chat(chat_client, f"How long is the unstaking period for vault {S2E_BONK_VAULT}?")
        assert_triggered(r, "S2E_GET_VAULT")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_vault_should_i_stake(self, chat_client):
        r = _chat(chat_client, (
            f"Should I stake in S2E vault {S2E_WIF_VAULT}? "
            "What are the key metrics?"
        ))
        assert_triggered(r, "S2E_GET_VAULT")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_vault_reward_token(self, chat_client):
        r = _chat(chat_client, f"S2E vault {S2E_JUP_VAULT} hangi token ile ödül veriyor?")
        assert_triggered(r, "S2E_GET_VAULT")
        assert_no_raw_json(r)

    def test_vault_total_staked(self, chat_client):
        r = _chat(chat_client, f"How much is currently staked in vault {S2E_JTO_VAULT}?")
        assert_triggered(r, "S2E_GET_VAULT")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# Dynamic Vault — get_info (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVaultGetInfo_Advanced:
    """Dynamic Vault get_info: 8 yeni senaryo"""

    def test_get_info_sol_vault(self, chat_client):
        r = _chat(chat_client, f"Get info for the SOL dynamic vault on Meteora (mint: {SOL_MINT})")
        assert_triggered(r, "VAULT_GET_INFO")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_get_info_usdc_vault(self, chat_client):
        r = _chat(chat_client, f"Meteora USDC vault bilgilerini göster, mint: {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_INFO")
        assert_no_raw_json(r)

    def test_get_info_msol_vault(self, chat_client):
        r = _chat(chat_client, f"mSOL dynamic vault hakkında bilgi al: {MSOL_MINT}")
        assert_triggered(r, "VAULT_GET_INFO")
        p = r.params_for("VAULT_GET_INFO")
        assert MSOL_MINT in str(p), f"mSOL mint parametreye girmedi. Params: {p}"

    def test_get_info_strategies_listed(self, chat_client):
        r = _chat(chat_client, f"What lending strategies does the USDC vault use? Mint: {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_INFO")
        assert_no_raw_json(r)
        assert_mentions(r, ["strategy", "strateji", "lending", "protocol", "marginfi", "solend"], min_hits=1)

    def test_get_info_jitoosol_vault(self, chat_client):
        r = _chat(chat_client, f"JitoSOL dynamic vault info: {JITOOSOL_MINT}")
        assert_triggered(r, "VAULT_GET_INFO")
        assert_no_raw_json(r)

    def test_get_info_total_assets(self, chat_client):
        r = _chat(chat_client, f"Meteora SOL vault'unun toplam aktifi ne kadar? {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_INFO")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_get_info_locked_profit(self, chat_client):
        r = _chat(chat_client, f"What is the locked profit in the USDC vault? {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_INFO")
        assert_no_raw_json(r)

    def test_get_info_response_quality(self, chat_client):
        r = _chat(chat_client, (
            f"Analyze the USDC Meteora dynamic vault for me. "
            f"Mint address: {USDC_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_INFO")
        assert_no_raw_json(r)
        assert r.has_text_content()
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# Dynamic Vault — get_addresses (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVaultGetAddresses_Advanced:
    """Dynamic Vault get_addresses: 7 yeni senaryo"""

    def test_addresses_sol_vault(self, chat_client):
        r = _chat(chat_client, f"Get all on-chain addresses for the SOL vault, mint: {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_ADDRESSES")
        assert_no_raw_json(r)

    def test_addresses_usdt_vault(self, chat_client):
        r = _chat(chat_client, f"Meteora USDT vault on-chain adreslerini göster: {USDT_MINT}")
        assert_triggered(r, "VAULT_GET_ADDRESSES")
        p = r.params_for("VAULT_GET_ADDRESSES")
        assert USDT_MINT in str(p), f"USDT mint parametreye girmedi. Params: {p}"

    def test_addresses_program_id_included(self, chat_client):
        r = _chat(chat_client, f"USDC vault için program ID ve token hesabı adreslerini bul: {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_ADDRESSES")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_addresses_msol_strategies(self, chat_client):
        r = _chat(chat_client, f"What strategy addresses does the mSOL vault have? {MSOL_MINT}")
        assert_triggered(r, "VAULT_GET_ADDRESSES")
        assert_no_raw_json(r)

    def test_addresses_response_has_addresses(self, chat_client):
        r = _chat(chat_client, f"Show me the vault account address for SOL: {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_ADDRESSES")
        assert_no_raw_json(r)

    def test_addresses_for_integration(self, chat_client):
        r = _chat(chat_client, (
            f"I'm integrating with Meteora vaults. "
            f"Give me the relevant addresses for USDC vault ({USDC_MINT})."
        ))
        assert_triggered(r, "VAULT_GET_ADDRESSES")
        assert_no_raw_json(r)
        assert r.has_text_content()

    def test_addresses_no_wrong_endpoint(self, chat_client):
        r = _chat(chat_client, f"Vault adresleri: {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_ADDRESSES")
        assert_not_triggered(r, "VAULT_GET_STATE")


# ═══════════════════════════════════════════════════════════════════════════════
# Dynamic Vault — get_state (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVaultGetState_Advanced:
    """Dynamic Vault get_state: 8 yeni senaryo"""

    def test_state_sol_vault(self, chat_client):
        r = _chat(chat_client, f"What is the current state of the SOL Meteora vault? {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_STATE")
        assert_param(r, "VAULT_GET_STATE", "tokenMint", contains=SOL_MINT)

    def test_state_usdc_vault(self, chat_client):
        r = _chat(chat_client, f"Meteora USDC vault'unun mevcut durumu nedir? {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_STATE")
        assert_param(r, "VAULT_GET_STATE", "tokenMint", contains=USDC_MINT)

    def test_state_usdt_vault(self, chat_client):
        r = _chat(chat_client, f"Get state for USDT vault, mint: {USDT_MINT}")
        assert_triggered(r, "VAULT_GET_STATE")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_state_total_amount(self, chat_client):
        r = _chat(chat_client, f"How much USDC is currently held in the Meteora vault? {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_STATE")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_state_lp_mint_info(self, chat_client):
        r = _chat(chat_client, f"What is the LP mint address for the SOL vault? {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_STATE")
        assert_no_raw_json(r)

    def test_state_enabled_status(self, chat_client):
        r = _chat(chat_client, f"Is the USDC Meteora vault currently active/enabled? {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_STATE")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_state_jitoosol_vault(self, chat_client):
        r = _chat(chat_client, f"JitoSOL vault'unun mevcut state'i: {JITOOSOL_MINT}")
        assert_triggered(r, "VAULT_GET_STATE")
        assert_param(r, "VAULT_GET_STATE", "tokenMint", contains=JITOOSOL_MINT)

    def test_state_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            f"I want to deposit USDC into Meteora vault. "
            f"Check if the vault is active: {USDC_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_STATE")
        assert_no_raw_json(r)
        assert r.has_text_content()


# ═══════════════════════════════════════════════════════════════════════════════
# Dynamic Vault — get_apy (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVaultGetApy_Advanced:
    """Dynamic Vault get_apy: 8 yeni senaryo"""

    def test_apy_sol_vault(self, chat_client):
        r = _chat(chat_client, f"What is the APY for the SOL Meteora vault? {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_APY")
        assert_param(r, "VAULT_GET_APY", "tokenMint", contains=SOL_MINT)
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_apy_usdc_vault(self, chat_client):
        r = _chat(chat_client, f"USDC vault APY oranını göster: {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_APY")
        assert_param(r, "VAULT_GET_APY", "tokenMint", contains=USDC_MINT)
        assert_mentions(r, ["apy", "apr", "yield", "%", "getiri"], min_hits=1)

    def test_apy_closest_value(self, chat_client):
        r = _chat(chat_client, f"What is the closest APY for USDT vault? {USDT_MINT}")
        assert_triggered(r, "VAULT_GET_APY")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_apy_average_vs_long(self, chat_client):
        r = _chat(chat_client, (
            f"Show me both the average and long-term APY for the SOL vault: {SOL_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_APY")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_mentions(r, ["average", "long", "ortalama", "uzun", "closest", "yakın"], min_hits=1)

    def test_apy_msol_vault(self, chat_client):
        r = _chat(chat_client, f"mSOL Meteora vault'unun APY değeri nedir? {MSOL_MINT}")
        assert_triggered(r, "VAULT_GET_APY")
        assert_no_raw_json(r)
        assert_no_hallucination(r)

    def test_apy_comparison_query(self, chat_client):
        r = _chat(chat_client, (
            f"Compare APY between SOL ({SOL_MINT}) and USDC ({USDC_MINT}) Meteora vaults"
        ))
        # LLM might trigger two separate calls or handle it
        triggered = r.all_triggered_types()
        assert "VAULT_GET_APY" in triggered, (
            f"VAULT_GET_APY tetiklenmedi. Tetiklenenler: {triggered}"
        )
        assert_no_raw_json(r)

    def test_apy_jitoosol_staking(self, chat_client):
        r = _chat(chat_client, f"JitoSOL vault'una yatırım yapmak istiyorum, APY nedir? {JITOOSOL_MINT}")
        assert_triggered(r, "VAULT_GET_APY")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_apy_response_no_hallucination(self, chat_client):
        r = _chat(chat_client, f"What kind of yield can I expect from the USDC vault? {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_APY")
        assert_no_hallucination(r)
        assert_no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# Dynamic Vault — get_apy_history (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVaultGetApyHistory_Advanced:
    """Dynamic Vault get_apy_history: 9 yeni senaryo"""

    def test_apy_history_last_7_days(self, chat_client):
        r = _chat(chat_client, f"Son 7 günün APY geçmişini göster: {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        p = r.params_for("VAULT_GET_APY_HISTORY")
        assert_timestamp_is_reasonable(p, "startTimestamp")
        assert_timestamp_is_reasonable(p, "endTimestamp")
        if "startTimestamp" in p and "endTimestamp" in p:
            assert int(p["startTimestamp"]) < int(p["endTimestamp"]), (
                f"start < end olmalı. start={p['startTimestamp']}, end={p['endTimestamp']}"
            )

    def test_apy_history_last_30_days(self, chat_client):
        r = _chat(chat_client, f"Show 30-day APY history for SOL vault: {SOL_MINT}")
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        p = r.params_for("VAULT_GET_APY_HISTORY")
        if "startTimestamp" in p:
            # 30 gün = ~2592000 saniye, başlangıç son 30 günden önce olmalı
            diff = NOW_TS - int(p["startTimestamp"])
            assert diff >= 25 * 86400, (
                f"30 günlük pencere için startTimestamp çok yakın. diff={diff}s"
            )

    def test_apy_history_timestamps_in_seconds(self, chat_client):
        r = _chat(chat_client, f"APY history for USDC vault last 14 days: {USDC_MINT}")
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        p = r.params_for("VAULT_GET_APY_HISTORY")
        for key in ["startTimestamp", "endTimestamp"]:
            if key in p:
                ts = int(p[key])
                assert ts < 10**11, (
                    f"'{key}'={ts} milisaniye cinsinden görünüyor (çok büyük), saniye olmalı"
                )

    def test_apy_history_sol_vault_trend(self, chat_client):
        r = _chat(chat_client, (
            f"SOL vault APY'si son bir ayda artış mı gösteri yoksa düşüş mü? "
            f"Mint: {SOL_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_no_hallucination(r)

    def test_apy_history_msol_3_months(self, chat_client):
        r = _chat(chat_client, f"3 aylık mSOL vault APY geçmişi: {MSOL_MINT}")
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        p = r.params_for("VAULT_GET_APY_HISTORY")
        if "startTimestamp" in p:
            diff = NOW_TS - int(p["startTimestamp"])
            assert diff >= 80 * 86400, (
                f"90 günlük pencere için startTimestamp çok yakın. diff={diff}s"
            )

    def test_apy_history_specific_date_range(self, chat_client):
        r = _chat(chat_client, (
            f"USDC vault APY history from Jan 1 2025 to Feb 1 2025: {USDC_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        p = r.params_for("VAULT_GET_APY_HISTORY")
        if "startTimestamp" in p and "endTimestamp" in p:
            start = int(p["startTimestamp"])
            end = int(p["endTimestamp"])
            assert start < end, f"start < end olmalı: {start} < {end}"
            # Jan 2025 ~ 1735689600
            assert 1735000000 < start < 1738000000, (
                f"startTimestamp Jan 2025 olmalı: {start}"
            )

    def test_apy_history_response_interpretation(self, chat_client):
        r = _chat(chat_client, (
            f"SOL vault APY geçmişini analiz et, en yüksek ve en düşük noktaları belirt. "
            f"Son 30 gün. Mint: {SOL_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        assert_no_raw_json(r)
        assert_has_number(r)

    def test_apy_history_jitoosol(self, chat_client):
        r = _chat(chat_client, f"JitoSOL vault APY history last week: {JITOOSOL_MINT}")
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        p = r.params_for("VAULT_GET_APY_HISTORY")
        assert_timestamp_is_reasonable(p, "startTimestamp")
        assert_no_raw_json(r)

    def test_apy_history_no_hallucination(self, chat_client):
        r = _chat(chat_client, (
            f"Bu vault'un geçen yıla göre APY trendi nasıl değişti? "
            f"USDC mint: {USDC_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_APY_HISTORY")
        assert_no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# Dynamic Vault — get_virtual_price (Advanced)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVaultGetVirtualPrice_Advanced:
    """Dynamic Vault get_virtual_price: 8 yeni senaryo"""

    def test_virtual_price_sol_strategy(self, chat_client):
        r = _chat(chat_client, (
            f"Get virtual price for SOL vault, "
            f"token: {SOL_MINT}, strategy: {SOL_STRATEGY_JITO}"
        ))
        assert_triggered(r, "VAULT_GET_VIRTUAL_PRICE")
        p = r.params_for("VAULT_GET_VIRTUAL_PRICE")
        assert SOL_MINT in str(p.get("tokenMint", "")), f"tokenMint eksik. Params: {p}"
        assert SOL_STRATEGY_JITO in str(p.get("strategy", "")), f"strategy eksik. Params: {p}"

    def test_virtual_price_usdc_strategy(self, chat_client):
        r = _chat(chat_client, (
            f"Virtual price for USDC vault strategy: "
            f"mint={USDC_MINT}, strategy={USDC_STRATEGY_MARGINFI}"
        ))
        assert_triggered(r, "VAULT_GET_VIRTUAL_PRICE")
        p = r.params_for("VAULT_GET_VIRTUAL_PRICE")
        assert USDC_MINT in str(p.get("tokenMint", "")), f"tokenMint eksik. Params: {p}"
        assert USDC_STRATEGY_MARGINFI in str(p.get("strategy", "")), f"strategy eksik. Params: {p}"

    def test_virtual_price_both_params_required(self, chat_client):
        r = _chat(chat_client, f"Şu vault stratejisinin sanal fiyatını al: {SOL_MINT}")
        # Strateji adresi olmadan LLM ya soru sormalı ya da uyarmalı
        if "VAULT_GET_VIRTUAL_PRICE" in r.all_triggered_types():
            p = r.params_for("VAULT_GET_VIRTUAL_PRICE")
            # Strateji varsa iyi
            pass
        else:
            assert r.asked_clarification() or r.has_text_content(), (
                "Eksik strategy için LLM tepki vermedi"
            )

    def test_virtual_price_explains_concept(self, chat_client):
        r = _chat(chat_client, (
            f"What is virtual price and how do I check it for the USDC vault? "
            f"USDC mint: {USDC_MINT}"
        ))
        assert r.has_text_content(), "Virtual price hakkında açıklama bekleniyor"
        assert_no_raw_json(r)

    def test_virtual_price_response_includes_value(self, chat_client):
        r = _chat(chat_client, (
            f"Virtual price for SOL vault strategy {SOL_STRATEGY_JITO}, "
            f"token mint {SOL_MINT}"
        ))
        assert_triggered(r, "VAULT_GET_VIRTUAL_PRICE")
        assert_no_raw_json(r)
        assert_has_number(r)
        assert_no_hallucination(r)

    def test_virtual_price_accrued_interest(self, chat_client):
        r = _chat(chat_client, (
            f"How much interest has accrued in the USDC lending strategy? "
            f"Token: {USDC_MINT}, strategy: {USDC_STRATEGY_MARGINFI}"
        ))
        assert_triggered(r, "VAULT_GET_VIRTUAL_PRICE")
        assert_no_raw_json(r)

    def test_virtual_price_strategy_from_info(self, chat_client):
        r = _chat(chat_client, (
            "Bir lending strategy'nin virtual price'ını öğrenmek istiyorum. "
            f"Token mint: {USDC_MINT}, strateji: {USDC_STRATEGY_MARGINFI}"
        ))
        assert_triggered(r, "VAULT_GET_VIRTUAL_PRICE")
        p = r.params_for("VAULT_GET_VIRTUAL_PRICE")
        assert USDC_MINT in str(p), f"tokenMint eksik. Params: {p}"

    def test_virtual_price_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            f"What does the virtual price tell me about how well the "
            f"USDC vault strategy is performing? "
            f"Mint: {USDC_MINT}, strategy: {USDC_STRATEGY_MARGINFI}"
        ))
        assert_triggered(r, "VAULT_GET_VIRTUAL_PRICE")
        assert_no_raw_json(r)
        assert r.has_text_content()
        assert_no_hallucination(r)

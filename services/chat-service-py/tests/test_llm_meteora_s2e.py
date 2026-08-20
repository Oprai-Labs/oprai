"""
Meteora Stake2Earn (m3m3) — Kapsamlı LLM Kalite Testi

Kapsanan 4 read endpoint:
  - meteora_s2e_get_analytics   (parametre yok)
  - meteora_s2e_get_all_vaults  (parametre yok)
  - meteora_s2e_filter_vaults   (poolAddress — isteğe bağlı, virgülle ayrılmış)
  - meteora_s2e_get_vault        (vaultAddress — zorunlu)

Test sınıfları:
  TestS2EGetAnalytics         — 14 test (global stats, TVL, fees, yorum kalitesi)
  TestS2EGetAllVaults         — 12 test (vault listesi, parametresiz, yorum)
  TestS2EFilterVaults         — 16 test (poolAddress doğru param, çoklu adres, hata)
  TestS2EGetVault             — 14 test (zorunlu adres, geçersiz adres, yorum derinliği)
  TestS2ERouting              — 16 test (analytics vs vaults, S2E vs DLMM/DAMM, belirsiz)
  TestS2EErrorHandling        — 12 test (eksik adres, geçersiz pubkey, >100 adres)
  TestS2EUserGuidance         — 12 test (Stake2Earn nedir, nasıl stake, vault bulma)
  TestS2EInterpretationDepth  — 12 test (analiz, karşılaştırma, kullanıcı yararı)

Toplam: ~108 test
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

# Bilinen Stake2Earn vault ve pool adresleri (mainnet)
S2E_BONK_VAULT = "7GDsGiNf8x1mP5zRAjqEb6o3ZVQFfqX1v8E2eNkzV8JL"
S2E_SOL_VAULT  = "3tG9BfqKs3JaDFmRvqHNDz6D2p6FCVW7uC6GsnKoJbKB"
DV1_SOL_USDC   = "HcjZvfeSNJbNkfLD4eEcVBr987W65zqKvvcaYkM7SkAs"
DV1_BONK_SOL   = "5rJhDkDQMmNn8D6mcGbFBq4dG7TSGcLFGMaqMJFJnygE"

FAKE_ADDR    = "FakeAddr111111111111111111111111111111111111"
GARBAGE_ADDR = "not-a-solana-address"


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


# ─── Helpers ─────────────────────────────────────────────────────────────

def assert_action_triggered(result: StreamResult, expected: str, msg: str = ""):
    triggered = result.all_triggered_types()
    assert expected in triggered, (
        f"'{expected}' tetiklenmedi.\n"
        f"Tetiklenenler: {triggered}\n"
        f"LLM metni: {result.text[:500]}\n{msg}"
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
            assert key in p, f"[{action}] '{key}' eksik.\nParams: {p}\n{msg}"
            if contains:
                assert contains.lower() in str(p[key]).lower(), (
                    f"[{action}] '{key}'={p[key]!r} does not contain '{contains}'.\n{msg}"
                )
            return
    pytest.fail(
        f"[{action}] tetiklenmedi.\nTetiklenenler: {result.all_triggered_types()}\n{msg}"
    )


def assert_no_raw_json(result: StreamResult, msg: str = ""):
    t = result.text
    assert not (t.count("{") + t.count("}") > 10 and t.count('"') > 20), (
        f"LLM dumped raw JSON.\nText: {t[:600]}\n{msg}"
    )


def assert_text_not_empty(result: StreamResult, msg: str = "", min_chars: int = 40):
    assert len(result.text.strip()) >= min_chars, (
        f"Response too short ({len(result.text)} chars).\nText: {result.text!r}\n{msg}"
    )


def assert_mentions(result: StreamResult, keywords: list[str], msg: str = "", min_hits: int = 1):
    lower = result.text_lower()
    hits = [kw for kw in keywords if kw.lower() in lower]
    assert len(hits) >= min_hits, (
        f"LLM said none of: {keywords}\nText: {result.text[:500]}\n{msg}"
    )


def assert_explains_error(result: StreamResult, msg: str = ""):
    kws = ["hata", "bulunamadı", "geçersiz", "error", "invalid", "not found",
           "unable", "cannot", "üzgün", "maalesef", "sorry"]
    lower = result.text_lower()
    assert any(k in lower for k in kws) or result.asked_clarification(), (
        f"No explanatory text on error.\nText: {result.text[:400]}\n{msg}"
    )


def assert_no_hallucination(result: StreamResult, msg: str = ""):
    phrases = [
        "gerçek zamanlı veriye erişimim yok",
        "güncel veriye sahip değilim",
        "i don't have access to real-time",
        "as of my knowledge cutoff",
        "i cannot access live",
    ]
    lower = result.text_lower()
    for p in phrases:
        assert p not in lower, (
            f"LLM veri çekmeden 'erişimim yok' dedi (hallucination).\n"
            f"Metin: {result.text[:400]}\n{msg}"
        )


def assert_no_wrong_filter_param(result: StreamResult, msg: str = ""):
    """filter_vaults'ta searchTerm/sortKey/orderBy/limit gönderilmemeli"""
    p = result.params_for("meteora_s2e_filter_vaults")
    wrong = [k for k in p if k in ("searchTerm", "search_term", "sortKey", "sort_key",
                                    "orderBy", "order_by", "limit", "offset")]
    assert not wrong, (
        f"filter_vaults artık bu parametreleri desteklemiyor ama gönderildi: {wrong}\n"
        f"Params: {p}\n{msg}"
    )


def result_has_response(r: StreamResult) -> bool:
    return r.has_text_content() or r.asked_clarification() or len(r.all_triggered_types()) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 1. GET ANALYTICS — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2EGetAnalytics:

    def test_genel_analytics_turkce(self, chat_client):
        msg = "Meteora Stake2Earn protokol analitiğini göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_no_hallucination(r, msg)

    def test_genel_analytics_ingilizce(self, chat_client):
        msg = "Show Meteora Stake2Earn analytics"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_toplam_tvl_sorusu(self, chat_client):
        msg = "Meteora Stake2Earn'in toplam kilitli değeri (TVL) nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)
        assert_no_hallucination(r, msg)
        assert_mentions(r, ["tvl", "total", "staked", "kilitli", "dolar", "$"], msg)

    def test_toplam_fee_dagitimi(self, chat_client):
        msg = "Meteora Stake2Earn protokolünde şimdiye kadar ne kadar ücret dağıtıldı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "ücret", "dağıt", "total", "distributed"], msg)

    def test_parametresiz_cagri(self, chat_client):
        """Analytics endpoint param almaz — LLM ekstra param göndermemeli"""
        msg = "Meteora m3m3 Stake2Earn genel istatistikleri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        p = r.params_for("meteora_s2e_get_analytics")
        assert p == {} or all(v is None for v in p.values()), (
            f"Analytics takes no params but got: {p}\n{msg}"
        )

    def test_yorum_kalitesi_anlamli_metin(self, chat_client):
        msg = "Meteora Stake2Earn protokolünün durumunu analiz et"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_no_hallucination(r, msg)

    def test_m3m3_keyword_routing(self, chat_client):
        msg = "Meteora m3m3 istatistiklerini göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)

    def test_staked_amount_usd_yorumu(self, chat_client):
        msg = "Meteora Stake2Earn'de ne kadar para stake edilmiş?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_analytics_not_vaults(self, chat_client):
        msg = "Meteora Stake2Earn protokol düzeyinde istatistikler (vault listesi değil)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_not_triggered(r, "meteora_s2e_get_all_vaults", msg)

    def test_analytics_not_dlmm(self, chat_client):
        msg = "Meteora Stake2Earn genel analitik verisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        triggered = r.all_triggered_types()
        assert not any("dlmm" in t for t in triggered), (
            f"The DLMM action was triggered by mistake: {triggered}\n{msg}"
        )

    def test_fee_vault_aciklamasi(self, chat_client):
        msg = "Meteora Stake2Earn kaç tane fee vault'u var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_analytics_no_raw_json(self, chat_client):
        msg = "Meteora Stake2Earn analitiğini anlaşılır şekilde özetle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=80)

    def test_protokol_saglik_degerlendirmesi(self, chat_client):
        msg = "Meteora Stake2Earn protokolünün sağlığını değerlendir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_analytics_no_hallucination_repeat(self, chat_client):
        msg = "Meteora m3m3 protokolüne dair güncel verileri getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_no_hallucination(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 2. GET ALL VAULTS — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2EGetAllVaults:

    def test_tum_vault_liste_turkce(self, chat_client):
        msg = "Meteora Stake2Earn vault'larının tümünü listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_tum_vault_ingilizce(self, chat_client):
        msg = "List all Meteora Stake2Earn vaults"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_text_not_empty(r, msg)

    def test_parametresiz_cagri(self, chat_client):
        """get_all_vaults param almaz"""
        msg = "Meteora m3m3 vault listesini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        p = r.params_for("meteora_s2e_get_all_vaults")
        assert p == {} or all(v is None for v in p.values()), (
            f"get_all_vaults takes no params but got: {p}\n{msg}"
        )

    def test_yorum_no_raw_json(self, chat_client):
        msg = "Meteora Stake2Earn vault'larını anlayabileceğim şekilde göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)

    def test_kac_vault_var(self, chat_client):
        msg = "Meteora Stake2Earn'de kaç tane vault mevcut?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_text_not_empty(r, msg)
        assert_no_hallucination(r, msg)

    def test_m3m3_vault_listing(self, chat_client):
        msg = "Meteora m3m3 vault'larını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_text_not_empty(r, msg)

    def test_vault_liste_not_analytics(self, chat_client):
        msg = "Meteora Stake2Earn'deki tüm vault'ları listele (istatistik değil, vault listesi)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_not_triggered(r, "meteora_s2e_get_analytics", msg)

    def test_vault_no_hallucination(self, chat_client):
        msg = "Meteora Stake2Earn vault listesini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_no_hallucination(r, msg)

    def test_vault_anlam_yorumu(self, chat_client):
        msg = "Meteora Stake2Earn vault'larını listele ve en fazla stake edilmiş olanı vurgula"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_vault_not_dammv1(self, chat_client):
        msg = "Meteora Stake2Earn vault'larını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        triggered = r.all_triggered_types()
        assert not any("dammv1" in t for t in triggered), (
            f"DAMM v1 eylemi yanlışlıkla tetiklendi: {triggered}\n{msg}"
        )

    def test_vault_liste_ozet(self, chat_client):
        msg = "Meteora m3m3 Stake2Earn vault listesini kısaca özetle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)

    def test_vault_liste_ingilizce_all(self, chat_client):
        msg = "Show me all available m3m3 Stake2Earn fee vaults"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_text_not_empty(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 3. FILTER VAULTS — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2EFilterVaults:

    def test_tek_pool_adresi_ile_filtre(self, chat_client):
        msg = f"Meteora Stake2Earn'de {DV1_SOL_USDC} havuzuna ait vault'u bul"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_param_present(r, "meteora_s2e_filter_vaults", "poolAddress", DV1_SOL_USDC, msg)
        assert_no_wrong_filter_param(r, msg)

    def test_iki_pool_adresi_virgul(self, chat_client):
        msg = (f"Meteora Stake2Earn'de şu iki havuzun vault'larını getir: "
               f"{DV1_SOL_USDC} ve {DV1_BONK_SOL}")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        p = r.params_for("meteora_s2e_filter_vaults")
        assert "poolAddress" in p, f"poolAddress eksik. Params: {p}\n{msg}"
        # Her iki adres de virgülle ayrılmış string'de bulunmalı
        val = str(p["poolAddress"])
        assert DV1_SOL_USDC in val or DV1_BONK_SOL in val, (
            f"Adresler poolAddress içinde yok: {val}\n{msg}"
        )
        assert_no_wrong_filter_param(r, msg)

    def test_ingilizce_pool_filtresi(self, chat_client):
        msg = f"Filter Meteora Stake2Earn vaults by pool address {DV1_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_param_present(r, "meteora_s2e_filter_vaults", "poolAddress", DV1_SOL_USDC, msg)

    def test_searchterm_gonderilmemeli(self, chat_client):
        """Eski searchTerm parametresi artık geçersiz — LLM gönderirse test başarısız"""
        msg = "Meteora Stake2Earn vault'larını BONK'a göre filtrele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_s2e_filter_vaults" in triggered:
            assert_no_wrong_filter_param(r, msg)

    def test_token_ismiyle_filtre_yoktur(self, chat_client):
        """filter_vaults sadece pool adresi kabul eder, token ismi değil"""
        msg = "Meteora Stake2Earn'de BONK vault'larını bul"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        # LLM ya get_all_vaults tetikleyip listeden seçmeli ya da clarify isteyip
        # adres sorgulamalı — filter_vaults tetiklerse searchTerm göndermemeli
        if "meteora_s2e_filter_vaults" in triggered:
            assert_no_wrong_filter_param(r, msg)
        else:
            # get_all_vaults tetiklenmiş olabilir — bu da kabul edilebilir
            assert result_has_response(r), f"LLM returned no response.\n{msg}"

    def test_filter_parametresiz_cagri(self, chat_client):
        """poolAddress vermeden çağrı da geçerli — tüm vault'ları döner"""
        msg = "Meteora Stake2Earn vault'larını filtrele (tümü)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_s2e_filter_vaults", "meteora_s2e_get_all_vaults"
        ]), f"Beklenen eylem tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        assert_text_not_empty(r, msg)

    def test_filter_yorum_no_raw_json(self, chat_client):
        msg = f"Meteora Stake2Earn'de {DV1_SOL_USDC} havuzuna ait vault'u getir ve açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)

    def test_pool_adresi_kopyalandi(self, chat_client):
        msg = f"Pool adresi {DV1_BONK_SOL} olan Stake2Earn vault'unu göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_param_present(r, "meteora_s2e_filter_vaults", "poolAddress", DV1_BONK_SOL, msg)

    def test_filter_not_analytics(self, chat_client):
        msg = f"Meteora Stake2Earn'de {DV1_SOL_USDC} pool adresiyle vault filtrele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_not_triggered(r, "meteora_s2e_get_analytics", msg)

    def test_no_hallucination_filter(self, chat_client):
        msg = f"Meteora Stake2Earn {DV1_SOL_USDC} havuzunun vault verilerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_no_hallucination(r, msg)

    def test_fake_pool_address_error(self, chat_client):
        msg = f"Meteora Stake2Earn'de {FAKE_ADDR} pool adresli vault'u filtrele"
        r = _chat(chat_client, msg)
        # LLM çağırır ama API boş/hata döner — LLM bunu açıklamalı
        triggered = r.all_triggered_types()
        if "meteora_s2e_filter_vaults" in triggered:
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_cok_adres_virgul_formati(self, chat_client):
        addr1 = DV1_SOL_USDC
        addr2 = DV1_BONK_SOL
        msg = f"Meteora Stake2Earn'de şu pool adreslerini filtrele: {addr1}, {addr2}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        p = r.params_for("meteora_s2e_filter_vaults")
        val = str(p.get("poolAddress", ""))
        assert "," in val or addr1 in val, (
            f"Çoklu adres virgülle gönderilmedi. poolAddress={val!r}\n{msg}"
        )

    def test_filter_anlam_yorumu_dolu(self, chat_client):
        msg = f"Meteora Stake2Earn'de {DV1_SOL_USDC} havuzunun vault'unu bul ve ne işe yaradığını açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_filter_not_dlmm(self, chat_client):
        msg = f"Meteora Stake2Earn pool filtresi: {DV1_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        triggered = r.all_triggered_types()
        assert not any("dlmm" in t for t in triggered), (
            f"DLMM tetiklendi: {triggered}\n{msg}"
        )

    def test_sortkeyless_filtre(self, chat_client):
        """Artık sort_key parametresi yok — LLM eskiden bunu gönderiyordu"""
        msg = f"Meteora Stake2Earn vault filtresi: pool={DV1_SOL_USDC}, TVL'ye göre sırala"
        r = _chat(chat_client, msg)
        if "meteora_s2e_filter_vaults" in r.all_triggered_types():
            assert_no_wrong_filter_param(r, msg)

    def test_filter_response_not_empty(self, chat_client):
        msg = f"Meteora Stake2Earn {DV1_BONK_SOL} pool vault verisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_text_not_empty(r, msg)
        assert_no_hallucination(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 4. GET ONE VAULT — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2EGetVault:

    def test_vault_adresi_ile_getir(self, chat_client):
        msg = f"Meteora Stake2Earn vault adresini getir: {S2E_BONK_VAULT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_param_present(r, "meteora_s2e_get_vault", "vaultAddress", S2E_BONK_VAULT, msg)

    def test_vault_ingilizce(self, chat_client):
        msg = f"Get details of Meteora Stake2Earn vault {S2E_BONK_VAULT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_param_present(r, "meteora_s2e_get_vault", "vaultAddress", S2E_BONK_VAULT, msg)

    def test_vault_adres_zorunlu(self, chat_client):
        """vaultAddress olmadan get_vault çağrılamaz — LLM sormalı"""
        msg = "Meteora Stake2Earn vault detaylarını göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_s2e_get_vault" in triggered:
            p = r.params_for("meteora_s2e_get_vault")
            assert "vaultAddress" in p and p["vaultAddress"], (
                f"vaultAddress zorunlu ama boş/eksik. Params: {p}\n{msg}"
            )
        else:
            # LLM adres soruyor olmalı
            assert result_has_response(r), f"LLM returned no response.\n{msg}"

    def test_vault_yorum_no_raw_json(self, chat_client):
        msg = f"Meteora Stake2Earn vault {S2E_BONK_VAULT} hakkında bilgi ver"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)

    def test_vault_no_hallucination(self, chat_client):
        msg = f"Meteora Stake2Earn vault {S2E_BONK_VAULT}'ün güncel verilerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_no_hallucination(r, msg)

    def test_vault_adresi_tam_aktarilmali(self, chat_client):
        """LLM vault adresini tam olarak params'a kopyalamalı — kesmemeli"""
        msg = f"Meteora m3m3 vault bilgisi: {S2E_SOL_VAULT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_param_present(r, "meteora_s2e_get_vault", "vaultAddress", S2E_SOL_VAULT, msg)
        # Adres tam mı?
        p = r.params_for("meteora_s2e_get_vault")
        assert p.get("vaultAddress") == S2E_SOL_VAULT, (
            f"Adres kesik/bozuk: {p.get('vaultAddress')!r} ≠ {S2E_SOL_VAULT}\n{msg}"
        )

    def test_fake_vault_address_hata_yorumu(self, chat_client):
        msg = f"Meteora Stake2Earn vault {FAKE_ADDR} detaylarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_garbage_vault_address(self, chat_client):
        msg = f"Meteora Stake2Earn vault '{GARBAGE_ADDR}' bilgisi"
        r = _chat(chat_client, msg)
        # LLM geçersiz adres tespit edip clarify isteyebilir ya da hata açıklayabilir
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_vault_not_analytics(self, chat_client):
        msg = f"Meteora Stake2Earn vault detayı: {S2E_BONK_VAULT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_not_triggered(r, "meteora_s2e_get_analytics", msg)

    def test_vault_not_all_vaults(self, chat_client):
        msg = f"Belirli bir Stake2Earn vault'u göster: {S2E_BONK_VAULT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_not_triggered(r, "meteora_s2e_get_all_vaults", msg)

    def test_vault_derinlik_yorumu(self, chat_client):
        msg = (f"Meteora Stake2Earn vault {S2E_BONK_VAULT} verilerini getir ve "
               f"stake miktarı ile fee gelirini açıkla")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_vault_kisa_aciklama_istegi(self, chat_client):
        msg = f"Meteora m3m3 vault {S2E_BONK_VAULT} ne kadar stake edilmiş?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_text_not_empty(r, msg)
        assert_no_hallucination(r, msg)

    def test_vault_adres_clarification(self, chat_client):
        """Adres belirtilmeden spesifik vault sorusu — LLM clarify isteyebilir"""
        msg = "Meteora Stake2Earn'deki tek bir vault'un detaylarını göster"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_vault_response_quality(self, chat_client):
        msg = (f"Meteora Stake2Earn vault {S2E_SOL_VAULT} hakkında "
               f"kullanıcı dostu bir özet yap")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=80)


# ══════════════════════════════════════════════════════════════════════════════
# 5. ROUTING TESTS — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2ERouting:

    def test_analytics_vs_vault_liste(self, chat_client):
        msg = "Meteora Stake2Earn protokol geneli istatistikler (vault listesi değil)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_not_triggered(r, "meteora_s2e_get_all_vaults", msg)

    def test_vault_liste_vs_analytics(self, chat_client):
        msg = "Meteora Stake2Earn vault'larını listele (protokol stats değil)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_not_triggered(r, "meteora_s2e_get_analytics", msg)

    def test_tek_vault_vs_vault_listesi(self, chat_client):
        msg = f"Meteora Stake2Earn tek vault detayı: {S2E_BONK_VAULT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_not_triggered(r, "meteora_s2e_get_all_vaults", msg)

    def test_pool_adresli_filtre_vs_get_all(self, chat_client):
        msg = f"Meteora Stake2Earn'de {DV1_SOL_USDC} havuzuna ait vault"
        r = _chat(chat_client, msg)
        # pool adresi varsa filter_vaults tercih edilmeli
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)

    def test_s2e_vs_dlmm_routing(self, chat_client):
        msg = "Meteora Stake2Earn vault istatistikleri (DLMM değil)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("s2e" in t for t in triggered), (
            f"S2E eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert not any("dlmm" in t for t in triggered), (
            f"DLMM was triggered by mistake: {triggered}\n{msg}"
        )

    def test_s2e_vs_dammv1_routing(self, chat_client):
        msg = "Meteora Stake2Earn analitik verileri (Dynamic AMM değil)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("s2e" in t for t in triggered), (
            f"S2E eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert not any("dammv1" in t for t in triggered), (
            f"DAMM v1 was triggered by mistake: {triggered}\n{msg}"
        )

    def test_dlmm_sorusu_s2e_tetiklememeli(self, chat_client):
        msg = "Meteora DLMM'deki pozisyonlarımı göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert not any("s2e" in t for t in triggered), (
            f"DLMM sorusunda S2E tetiklendi: {triggered}\n{msg}"
        )

    def test_dammv1_sorusu_s2e_tetiklememeli(self, chat_client):
        msg = "Meteora Dynamic AMM havuzlarını listele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert not any("s2e" in t for t in triggered), (
            f"DAMM v1 sorusunda S2E tetiklendi: {triggered}\n{msg}"
        )

    def test_filter_vault_adres_gerektirir(self, chat_client):
        """Token ismiyle filter_vaults çağrılamaz — adres gerekir"""
        msg = "Meteora Stake2Earn'de USDC vault'larını filtrele"
        r = _chat(chat_client, msg)
        if "meteora_s2e_filter_vaults" in r.all_triggered_types():
            assert_no_wrong_filter_param(r, msg)
            p = r.params_for("meteora_s2e_filter_vaults")
            # searchTerm ya da USDC içeriyorsa yanlış
            pa = str(p.get("poolAddress", ""))
            assert "USDC" not in pa or len(pa) >= 32, (
                f"Token ismi poolAddress olarak gönderildi: {pa}\n{msg}"
            )

    def test_analytics_tvl_vs_vault_staked(self, chat_client):
        msg = "Meteora Stake2Earn toplam TVL kaç dolar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)

    def test_m3m3_keyword_s2e_routing(self, chat_client):
        msg = "Meteora m3m3 protokolünün vault'larını listele"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("s2e" in t for t in triggered), (
            f"m3m3 sorusunda S2E tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )

    def test_fee_revenue_analytics_routing(self, chat_client):
        msg = "Meteora Stake2Earn'de toplam fee geliri ne kadar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)

    def test_vault_address_given_get_vault(self, chat_client):
        msg = f"Meteora Stake2Earn vault bilgisi: adres={S2E_SOL_VAULT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_not_triggered(r, "meteora_s2e_filter_vaults", msg)

    def test_filter_vs_get_one_routing(self, chat_client):
        """Pool adresi verilmişse filter_vaults; vault adresi verilmişse get_vault"""
        msg = f"Bu pool adresinin Stake2Earn vault'unu göster: {DV1_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)

    def test_analytics_no_vault_address_param(self, chat_client):
        msg = "Meteora Stake2Earn protokolünün genel metrikleri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        p = r.params_for("meteora_s2e_get_analytics")
        assert "vaultAddress" not in p, (
            f"analytics'e vaultAddress gönderilmemeli: {p}\n{msg}"
        )

    def test_dammv2_sorusu_s2e_tetiklememeli(self, chat_client):
        msg = "Meteora DAMM v2 havuzlarını göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert not any("s2e" in t for t in triggered), (
            f"DAMM v2 sorusunda S2E tetiklendi: {triggered}\n{msg}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 6. ERROR HANDLING — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2EErrorHandling:

    def test_get_vault_garbage_address(self, chat_client):
        msg = f"Meteora Stake2Earn vault '{GARBAGE_ADDR}' detayı"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_filter_garbage_pool_address(self, chat_client):
        msg = f"Meteora Stake2Earn'de '{GARBAGE_ADDR}' pool adresiyle filtrele"
        r = _chat(chat_client, msg)
        if "meteora_s2e_filter_vaults" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_get_vault_no_address_clarify(self, chat_client):
        msg = "Meteora Stake2Earn vault detayını getir"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)
        # Adres sormalı ya da get_all_vaults tetiklemeli
        triggered = r.all_triggered_types()
        if "meteora_s2e_get_vault" in triggered:
            p = r.params_for("meteora_s2e_get_vault")
            assert "vaultAddress" in p and p["vaultAddress"], (
                f"vaultAddress boş/eksik: {p}\n{msg}"
            )

    def test_filter_100_den_fazla_adres(self, chat_client):
        addrs = ",".join([f"Addr{str(i).zfill(40)}" for i in range(101)])
        msg = f"Meteora Stake2Earn'de şu 101 pool adresini filtrele: {addrs[:200]}..."
        r = _chat(chat_client, msg)
        # LLM ya uyarı vermeli ya da makul bir alt küme göndermeli
        assert result_has_response(r), f"LLM returned no response.\n{msg}"

    def test_analytics_extra_param_yok(self, chat_client):
        msg = "Meteora Stake2Earn analytics verisi (adres filtresiyle)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        p = r.params_for("meteora_s2e_get_analytics")
        assert p == {} or all(v is None for v in p.values()), (
            f"Analytics takes no params but got: {p}\n{msg}"
        )

    def test_get_all_vaults_extra_param_yok(self, chat_client):
        msg = "Meteora Stake2Earn vault listesi (BONK filtreli)"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_s2e_get_all_vaults" in triggered:
            p = r.params_for("meteora_s2e_get_all_vaults")
            assert p == {} or all(v is None for v in p.values()), (
                f"get_all_vaults takes no params but got: {p}\n{msg}"
            )

    def test_filter_eski_sort_key_yok(self, chat_client):
        msg = f"Meteora Stake2Earn vault'larını TVL sıralı filtrele: pool={DV1_SOL_USDC}"
        r = _chat(chat_client, msg)
        if "meteora_s2e_filter_vaults" in r.all_triggered_types():
            assert_no_wrong_filter_param(r, msg)

    def test_vault_fake_address_not_crash(self, chat_client):
        msg = f"Meteora Stake2Earn vault {FAKE_ADDR}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_filter_vault_boluk_adres(self, chat_client):
        msg = "Meteora Stake2Earn vault filtrele — pool adresi: (boş)"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"

    def test_analytics_hata_yumusak_yanit(self, chat_client):
        msg = "Meteora Stake2Earn analytics verisini API'dan çek"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_get_vault_kisa_adres(self, chat_client):
        msg = "Meteora Stake2Earn vault abc'yi göster"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_filter_eski_offset_limit_yok(self, chat_client):
        msg = f"Meteora Stake2Earn vault filtrele pool={DV1_SOL_USDC}, limit=10 offset=0"
        r = _chat(chat_client, msg)
        if "meteora_s2e_filter_vaults" in r.all_triggered_types():
            assert_no_wrong_filter_param(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 7. USER GUIDANCE — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2EUserGuidance:

    def test_stake2earn_ne_demek(self, chat_client):
        msg = "Meteora Stake2Earn (m3m3) nedir? Nasıl çalışır?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["stake", "fee", "ücret", "earn", "kazanç"], msg, min_hits=2)

    def test_nasil_stake_edilir(self, chat_client):
        msg = "Meteora Stake2Earn'e nasıl stake yaparım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_vault_nasil_bulunur(self, chat_client):
        msg = "Meteora Stake2Earn'de stake yapmak için vault adresini nasıl bulurum?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        # LLM get_all_vaults önerebilir ya da hemen çalıştırabilir
        assert result_has_response(r), f"LLM returned no response.\n{msg}"

    def test_cooldown_aciklamasi(self, chat_client):
        msg = "Meteora Stake2Earn'de unstake sonrasında ne oluyor? Cooldown var mı?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_mentions(r, ["cooldown", "unstake", "withdraw", "bekle", "süre"], msg)

    def test_fee_kazanc_aciklamasi(self, chat_client):
        msg = "Meteora Stake2Earn'de ne tür ücret kazanabilirim?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_mentions(r, ["fee", "ücret", "trading", "havuz", "pool"], msg)

    def test_m3m3_vs_diger_protokol(self, chat_client):
        msg = "Meteora m3m3 Stake2Earn ile klasik staking arasındaki fark nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_hangi_token_stake_edilebilir(self, chat_client):
        msg = "Meteora Stake2Earn'de hangi tokenları stake edebilirim?"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_vault_bul_ve_stake_rehberi(self, chat_client):
        msg = "Meteora Stake2Earn'de ilk kez stake yapacağım, adım adım anlat"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_risk_aciklamasi(self, chat_client):
        msg = "Meteora Stake2Earn'de stake yapmanın riskleri nelerdir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_analytics_anlam_aciklamasi(self, chat_client):
        msg = "Meteora Stake2Earn analitik verisindeki 'total_fee_vaults' ne anlama gelir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_pool_address_vs_vault_address_fark(self, chat_client):
        msg = "Meteora Stake2Earn'de pool adresi ile vault adresi arasındaki fark nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_claim_fee_aciklamasi(self, chat_client):
        msg = "Meteora Stake2Earn'de ödüllerimi nasıl talep ederim?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["claim", "talep", "ödül", "fee", "reward"], msg)


# ══════════════════════════════════════════════════════════════════════════════
# 8. INTERPRETATION DEPTH — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestS2EInterpretationDepth:

    def test_analytics_derinlik_yorumu(self, chat_client):
        msg = "Meteora Stake2Earn analitiklerini getir ve protokolün sağlığını değerlendir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg, min_chars=120)
        assert_no_raw_json(r, msg)
        assert_no_hallucination(r, msg)

    def test_vault_listesi_derinlik_yorumu(self, chat_client):
        msg = "Meteora Stake2Earn vault listesini getir ve en çok stake edilmiş top 3'ü analiz et"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_all_vaults", msg)
        assert_text_not_empty(r, msg, min_chars=120)
        assert_no_raw_json(r, msg)

    def test_tek_vault_derinlik_yorumu(self, chat_client):
        msg = (f"Meteora Stake2Earn vault {S2E_BONK_VAULT} verilerini getir; "
               f"stake miktarı, birikim hızı ve fee gelirini değerlendir")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_filter_sonucu_yorum(self, chat_client):
        msg = (f"Meteora Stake2Earn'de {DV1_SOL_USDC} havuzunun vault'unu bul ve "
               f"stake etmeye değer mi açıkla")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_filter_vaults", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_toplam_fee_usd_yorumu(self, chat_client):
        msg = "Meteora Stake2Earn'de toplam kilitli dolar değerini getir ve yorumla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_no_hallucination(r, msg)

    def test_yatirim_karari_vault(self, chat_client):
        msg = (f"Meteora Stake2Earn vault {S2E_SOL_VAULT} için stake etmeli miyim? "
               f"Veriyi getir ve tavsiye ver")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_vault_karsilastirma(self, chat_client):
        msg = (f"Meteora Stake2Earn vault listesini getir ve {S2E_BONK_VAULT} ile "
               f"{S2E_SOL_VAULT}'u karşılaştır")
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("s2e" in t for t in triggered), (
            f"S2E eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_analytics_ve_vault_kombine_analiz(self, chat_client):
        msg = "Meteora Stake2Earn protokolünün hem genel istatistiklerini hem vault listesini analiz et"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("s2e" in t for t in triggered), (
            f"S2E eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_fee_geliri_potansiyel(self, chat_client):
        msg = f"Meteora Stake2Earn vault {S2E_BONK_VAULT}'da ne kadar fee kazanabilirim?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_analytics_trend_analizi(self, chat_client):
        msg = "Meteora Stake2Earn güncel analitiklerini getir ve büyüme trendini yorumla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_analytics", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_vault_staked_amount_anlami(self, chat_client):
        msg = (f"Meteora Stake2Earn vault {S2E_BONK_VAULT} için staked_amount değerini "
               f"getir ve bu rakamın ne anlama geldiğini açıkla")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_s2e_get_vault", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_genel_durum_ozeti_s2e(self, chat_client):
        msg = "Meteora Stake2Earn protokolünün bugünkü genel durumunu kısa ve net anlat"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("s2e" in t for t in triggered), (
            f"S2E eylemi tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        )
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_no_hallucination(r, msg)

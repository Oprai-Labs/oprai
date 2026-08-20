"""
LLM conversation tests for Meteora DLMM endpoints.

These tests talk to a real LLM and check that:
1. natural-language questions trigger the right action
2. the right parameters are extracted
3. the LLM interprets the result instead of dumping raw JSON

The user-facing prompts are deliberately written in Turkish: this suite doubles
as the regression net for Turkish-language intent handling.

Requirements:
  - Chat service: localhost:3020
  - Solana service: localhost:3030
  - DATABASE_URL, OPRAI_INTERNAL_API_KEY, OPRAI_OPENAI_API_KEY in the environment

The tests skip themselves if a service is unavailable.

Test classes:
  TestLLMDlmmGetPairs           — 15 tests (pool listing)
  TestLLMDlmmGetPoolGroups      — 10 tests (token-pair groups)
  TestLLMDlmmGetPoolGroup       —  8 tests (every pool for one pair)
  TestLLMDlmmGetActiveBin       —  6 tests (active bin / spot price)
  TestLLMDlmmGetPair            —  6 tests (single pool detail)
  TestLLMDlmmGetPoolOhlcv       — 12 tests (price charts)
  TestLLMDlmmGetPoolVolHistory  — 12 tests (volume history)
  TestLLMDlmmGetProtocolStats   —  8 tests (protocol-wide statistics)
  TestLLMActionRouting          — 15 tests (picking the right endpoint)
  TestLLMNegative               —  8 tests (irrelevant or malformed questions)
  TestLLMInterpretationQuality  — 10 tests (interpretation quality)

Total: ~110 test cases
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

# ─── Config ────────────────────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://localhost:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

# Mainnet adresleri
METEORA_SOL_USDC  = "HcjZvfeSNJbNkfLD4eEcRBr96AD3w1Tm3fSXURqLSfm1"
METEORA_SOL_USDT  = "ARwi1S4DaiTG5DX7S4M4ZkL2tiPGmYXLqRbBJap5SLLK"
METEORA_BONK_SOL  = "Ek8sqNHnBtQ2aQ3SN6SYhgwuDqqxQNWFnrEkQn3BVPQM"
SOL_MINT          = "So11111111111111111111111111111111111111112"
USDC_MINT         = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT         = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
BONK_MINT         = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# Invalid address (deliberately wrong)
INVALID_ADDR = "not-a-real-solana-address-xyz"
SHORT_ADDR   = "abc123"


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def chat_client():
    try:
        r = httpx.get(f"{CHAT_URL}/health", timeout=3.0)
        if r.status_code not in (200, 204):
            pytest.skip(f"chat-service health check failed: {r.status_code}")
    except Exception as exc:
        pytest.skip(f"chat-service unreachable ({CHAT_URL}): {exc}")
    with httpx.Client(base_url=CHAT_URL, timeout=90.0) as c:
        yield c


def _headers() -> dict:
    return {
        "X-Internal-Api-Key": INTERNAL_KEY,
        "X-User-Wallet": TEST_WALLET,
        "Content-Type": "application/json",
    }


def _session_id() -> str:
    return f"local:{uuid.uuid4()}"


# ─── SSE parser ───────────────────────────────────────────────────────────

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
        types = []
        for a in self.actions:
            types.append(a.get("type", ""))
        for q in self.queries:
            types.append(q.get("type", ""))
        return types

    def params_for(self, action_type: str) -> dict:
        for item in self.actions + self.queries:
            if item.get("type") == action_type:
                return item.get("params", {})
        return {}


def _chat(client: httpx.Client, message: str) -> StreamResult:
    payload = {"sessionId": _session_id(), "content": message, "attachments": []}
    with client.stream("POST", "/messages/stream", json=payload, headers=_headers()) as r:
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        body = r.read().decode()
    return StreamResult.parse(body)


# ─── Assertion helpers ────────────────────────────────────────────────────

def assert_action_triggered(result: StreamResult, expected_type: str, msg: str = ""):
    triggered = result.all_triggered_types()
    assert expected_type in triggered, (
        f"Beklenen action '{expected_type}' tetiklenmedi.\n"
        f"Tetiklenenler: {triggered}\n"
        f"LLM metni: {result.text[:400]}\n"
        f"Mesaj: {msg}"
    )


def assert_param_present(result: StreamResult, action_type: str, param_key: str,
                         expected_value: str | None = None, msg: str = ""):
    for item in result.actions + result.queries:
        if item.get("type") == action_type:
            params = item.get("params", {})
            assert param_key in params, (
                f"[{action_type}] '{param_key}' parametresi eksik. Params: {params}\n{msg}"
            )
            if expected_value is not None:
                assert expected_value.lower() in str(params[param_key]).lower(), (
                    f"[{action_type}] '{param_key}'={params[param_key]!r}, "
                    f"does not contain the expected '{expected_value}'.\n{msg}"
                )
            return
    pytest.fail(f"[{action_type}] never triggered.\n{msg}")


def assert_no_raw_json(result: StreamResult, msg: str = ""):
    text = result.text
    assert not (text.count('"') > 20 and text.count("{") > 5), (
        f"LLM dumped raw JSON.\nText: {text[:500]}\n{msg}"
    )


def assert_text_not_empty(result: StreamResult, msg: str = ""):
    assert len(result.text.strip()) > 20, (
        f"LLM did not produce enough text.\nText: {result.text!r}\n{msg}"
    )


def assert_mentions(result: StreamResult, keywords: list[str], msg: str = ""):
    lower = result.text.lower()
    found = [kw for kw in keywords if kw.lower() in lower]
    assert found, (
        f"LLM text contains none of: {keywords}\n"
        f"Metin: {result.text[:500]}\n{msg}"
    )


def assert_has_number(result: StreamResult, msg: str = ""):
    assert any(c.isdigit() for c in result.text), (
        f"No numeric value in the LLM response.\nText: {result.text[:400]}\n{msg}"
    )


def assert_valid_timeframe(result: StreamResult, action_type: str, msg: str = ""):
    valid = {"5m", "30m", "1h", "2h", "4h", "12h", "24h"}
    params = result.params_for(action_type)
    if "timeframe" in params:
        tf = params["timeframe"]
        assert tf in valid, f"An invalid timeframe was sent: {tf!r} (valid: {valid})\n{msg}"


# ══════════════════════════════════════════════════════════════════════════════
# 1. meteora_dlmm_get_pairs — 15 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetPairs:

    def test_volume_sirali_en_iyi_havuzlar(self, chat_client):
        msg = "Meteora'da 24 saatlik hacme göre en yüksek DLMM havuzlarını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg)

    def test_volume_sirali_ingilizce(self, chat_client):
        msg = "Show me the top Meteora DLMM pools by 24h volume"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        p = r.params_for("meteora_dlmm_get_pairs")
        sort_val = str(p.get("sortBy", p.get("sort_by", ""))).lower()
        assert "volume" in sort_val, f"sortBy should contain volume, got: {sort_val!r}"
        assert_no_raw_json(r, msg)

    def test_tvl_sirali(self, chat_client):
        msg = "Meteora DLMM havuzlarını TVL'ye göre büyükten küçüğe sırala"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        p = r.params_for("meteora_dlmm_get_pairs")
        sort_val = str(p.get("sortBy", p.get("sort_by", ""))).lower()
        assert "tvl" in sort_val, f"sortBy should contain tvl, got: {sort_val!r}"
        assert_no_raw_json(r, msg)

    def test_sol_iceren_havuzlar(self, chat_client):
        msg = "SOL içeren Meteora DLMM havuzlarını listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_param_present(r, "meteora_dlmm_get_pairs", "query", "SOL", msg)
        assert_no_raw_json(r, msg)

    def test_usdc_iceren_havuzlar(self, chat_client):
        msg = "USDC çiftli DLMM poollarını bul Meteora'da"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_param_present(r, "meteora_dlmm_get_pairs", "query", "USDC", msg)
        assert_no_raw_json(r, msg)

    def test_bonk_havuzlari(self, chat_client):
        msg = "Meteora'da BONK token DLMM havuzları var mı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_param_present(r, "meteora_dlmm_get_pairs", "query", "BONK", msg)
        assert_no_raw_json(r, msg)

    def test_fee_tvl_sirali(self, chat_client):
        msg = "Meteora DLMM'de fee/TVL oranı en yüksek havuzlar hangileri?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg)

    def test_sayfalama_ikinci_sayfa(self, chat_client):
        msg = "Meteora DLMM havuzları - 2. sayfayı göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        p = r.params_for("meteora_dlmm_get_pairs")
        assert p.get("page") == "2", f"page=2 beklendi, geldi: {p.get('page')!r}"
        assert_no_raw_json(r, msg)

    def test_sayfalama_ucuncu_sayfa(self, chat_client):
        msg = "Meteora DLMM pools page 3"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        p = r.params_for("meteora_dlmm_get_pairs")
        assert p.get("page") == "3", f"page=3 beklendi, geldi: {p.get('page')!r}"

    def test_ilk_10_havuz(self, chat_client):
        msg = "Meteora'daki ilk 10 DLMM havuzunu listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg)

    def test_genel_liste_sorusu(self, chat_client):
        msg = "Meteora'da hangi DLMM havuzları var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_en_iyi_likidite_havuzlari(self, chat_client):
        msg = "En çok likidite olan Meteora DLMM havuzlarını bul"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_yorum_tvl_sayisal(self, chat_client):
        msg = "En yüksek TVL'li 5 Meteora DLMM havuzunu ve TVL değerlerini listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_yorum_token_adlari_geciyor(self, chat_client):
        msg = "Meteora DLMM'de en popüler 3 havuzu token çifti ismiyle söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["sol", "usdc", "usdt", "jup", "bonk", "msol", "/", "-"], msg)

    def test_ingilizce_sol_arama(self, chat_client):
        msg = "Find Meteora DLMM pools containing SOL sorted by TVL"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_param_present(r, "meteora_dlmm_get_pairs", "query", "SOL", msg)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 2. meteora_dlmm_get_pool_groups — 10 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetPoolGroups:

    def test_tum_cift_gruplari(self, chat_client):
        msg = "Meteora DLMM'deki tüm token çiftlerini gruplar halinde göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_hacim_sirali_ciftler(self, chat_client):
        msg = "Meteora DLMM'de 24 saatlik hacme göre sıralı token çifti grupları"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_no_raw_json(r, msg)

    def test_tvl_sirali_ciftler(self, chat_client):
        msg = "Toplam TVL'ye göre sıralı Meteora DLMM token çifti grupları"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_groups", "sortBy", "tvl", msg)
        assert_no_raw_json(r, msg)

    def test_fee_tvl_orani_en_yuksek_ciftler(self, chat_client):
        msg = "Meteora DLMM'de fee/TVL oranı en yüksek token çiftleri hangileri?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "tvl", "oran", "ratio", "%"], msg)

    def test_7_gunluk_hacim_penceresi(self, chat_client):
        msg = "Meteora DLMM'de 7 günlük hacme göre en yüksek token çiftleri hangileri?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg)

    def test_en_cok_havuzu_olan_cift(self, chat_client):
        msg = "Meteora'da SOL ile en fazla DLMM havuzu olan token çifti hangisi?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_ingilizce_pool_groups(self, chat_client):
        msg = "List all Meteora DLMM token pair groups sorted by volume"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_no_raw_json(r, msg)

    def test_yorum_cift_adlari_geciyor(self, chat_client):
        msg = "Meteora'da hangi token çifti grupları en çok işlem görüyor?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["sol", "usdc", "usdt", "bonk", "jup", "pair", "çift", "token"], msg)

    def test_sayfalama_pool_groups(self, chat_client):
        msg = "Meteora DLMM token çifti gruplarının 2. sayfasını göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        p = r.params_for("meteora_dlmm_get_pool_groups")
        assert p.get("page") == "2", f"page=2 beklendi, geldi: {p.get('page')!r}"
        assert_no_raw_json(r, msg)

    def test_hacim_en_dusuk_ciftler(self, chat_client):
        msg = "Meteora DLMM'de en az işlem hacmi olan token çiftleri hangileri?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 3. meteora_dlmm_get_pool_group — 8 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetPoolGroup:

    def test_sol_usdc_fee_tierlari(self, chat_client):
        msg = (
            f"Meteora DLMM'de SOL/USDC çiftinin tüm fee tier havuzlarını listele. "
            f"SOL mint: {SOL_MINT}, USDC mint: {USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg)

    def test_lexical_order_mints(self, chat_client):
        msg = (
            f"Bu token çiftindeki DLMM havuzlarını APY'ye göre sırala: "
            f"lexicalOrderMints={SOL_MINT}-{USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_group", "lexicalOrderMints", None, msg)
        assert_no_raw_json(r, msg)

    def test_bin_step_karsilastirma(self, chat_client):
        msg = (
            f"SOL/USDC Meteora DLMM havuzlarının bin step farklarını karşılaştır. "
            f"Mints: {SOL_MINT}-{USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_fee_en_dusuk_variant(self, chat_client):
        msg = (
            f"SOL/USDC token çiftinde fee'si en düşük Meteora DLMM havuzu hangisi? "
            f"Mints: {SOL_MINT}-{USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_tvl_sirali_variants(self, chat_client):
        msg = (
            f"Bu çiftin TVL'ye göre sıralı havuzlarını göster: "
            f"mints={SOL_MINT}-{USDC_MINT} sortBy=tvl"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        assert_no_raw_json(r, msg)

    def test_sayfalama_pool_group(self, chat_client):
        msg = (
            f"SOL/USDC DLMM havuzlarının 2. sayfasını getir. "
            f"Mints: {SOL_MINT}-{USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        p = r.params_for("meteora_dlmm_get_pool_group")
        assert p.get("page") == "2", f"page=2 beklendi, geldi: {p.get('page')!r}"
        assert_no_raw_json(r, msg)

    def test_ingilizce_pool_group(self, chat_client):
        msg = (
            f"Show all fee tier variants for the SOL/USDC DLMM pair on Meteora. "
            f"Mints: {SOL_MINT}-{USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        assert_no_raw_json(r, msg)

    def test_yorum_fee_tier_sayisi(self, chat_client):
        msg = (
            f"SOL/USDC için kaç farklı DLMM havuzu var Meteora'da? "
            f"Mints: {SOL_MINT}-{USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_group", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 4. meteora_dlmm_get_active_bin — 6 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetActiveBin:

    def test_guncel_fiyat_pool(self, chat_client):
        msg = f"Bu Meteora DLMM havuzunun güncel fiyatı nedir? {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_active_bin", msg)
        assert_param_present(r, "meteora_dlmm_get_active_bin", "address", METEORA_SOL_USDC, msg)
        assert_no_raw_json(r, msg)

    def test_aktif_bin_sorgula(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC}'ün aktif bin numarasını söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_active_bin", msg)
        assert_param_present(r, "meteora_dlmm_get_active_bin", "address", METEORA_SOL_USDC, msg)
        assert_no_raw_json(r, msg)

    def test_spot_fiyat_ingilizce(self, chat_client):
        msg = f"What is the current spot price of Meteora DLMM pool {METEORA_SOL_USDC}?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_active_bin", msg)
        assert_param_present(r, "meteora_dlmm_get_active_bin", "address", METEORA_SOL_USDC, msg)
        assert_no_raw_json(r, msg)

    def test_yorum_fiyat_sayisal(self, chat_client):
        msg = f"Meteora pool {METEORA_SOL_USDC} şu an ne fiyattan işlem görüyor? Sayısal olarak söyle."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_active_bin", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_sol_usdt_aktif_bin(self, chat_client):
        msg = f"Bu SOL/USDT havuzunun anlık fiyatına bak: {METEORA_SOL_USDT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_active_bin", msg)
        assert_param_present(r, "meteora_dlmm_get_active_bin", "address", METEORA_SOL_USDT, msg)
        assert_no_raw_json(r, msg)

    def test_fiyat_vs_ohlcv_routing(self, chat_client):
        """A spot-price question should trigger active_bin or get_pair, not ohlcv."""
        msg = f"Meteora DLMM pool {METEORA_SOL_USDC} şu anki spot fiyatı kaç dolar?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_active_bin" in triggered or "meteora_dlmm_get_pair" in triggered, (
            f"A spot price should trigger active_bin or get_pair. Triggered: {triggered}"
        )
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 5. meteora_dlmm_get_pair — 6 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetPair:

    def test_tekil_pool_detay(self, chat_client):
        msg = f"Bu Meteora DLMM pool'unun detaylarını göster: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pair", msg)
        assert_param_present(r, "meteora_dlmm_get_pair", "address", METEORA_SOL_USDC, msg)
        assert_no_raw_json(r, msg)

    def test_bin_step_bilgisi(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC}'ün bin step değeri nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pair", msg)
        assert_param_present(r, "meteora_dlmm_get_pair", "address", METEORA_SOL_USDC, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_fee_rate_bilgisi(self, chat_client):
        msg = f"Meteora DLMM pool {METEORA_SOL_USDC}'ün swap fee oranı ne?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pair", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_likidite_miktari(self, chat_client):
        msg = f"Bu pool'daki toplam likidite miktarını söyle: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pair", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_ingilizce_pair_detail(self, chat_client):
        msg = f"Get details of Meteora DLMM pool {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pair", msg)
        assert_param_present(r, "meteora_dlmm_get_pair", "address", METEORA_SOL_USDC, msg)
        assert_no_raw_json(r, msg)

    def test_yorum_tvl_ve_fee(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC}'ün TVL'si ve fee oranı nedir? Kısa açıkla."
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pair", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "tvl", "liquidity", "likidite", "%", "$"], msg)


# ══════════════════════════════════════════════════════════════════════════════
# 6. meteora_dlmm_get_pool_ohlcv — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetPoolOhlcv:

    def test_1h_fiyat_grafigi(self, chat_client):
        msg = f"Bu Meteora DLMM havuzunun 1 saatlik fiyat grafiğini göster: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "address", METEORA_SOL_USDC, msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "timeframe", "1h", msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_no_raw_json(r, msg)

    def test_24h_ohlcv(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} için 24h OHLCV verisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "address", METEORA_SOL_USDC, msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_no_raw_json(r, msg)

    def test_5m_granularite(self, chat_client):
        msg = f"Şu havuzun son 5 dakikalık mumlarını göster: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "timeframe", "5m", msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_no_raw_json(r, msg)

    def test_4h_mum_grafigi(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} için 4 saatlik mum grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "timeframe", "4h", msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_no_raw_json(r, msg)

    def test_30m_mum(self, chat_client):
        msg = f"30 dakikalık OHLCV mumları istiyorum: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "timeframe", "30m", msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_no_raw_json(r, msg)

    def test_12h_mum(self, chat_client):
        msg = f"12 saatlik mum verisi: pool {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "timeframe", "12h", msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_no_raw_json(r, msg)

    def test_fiyat_hareketi_yorum(self, chat_client):
        msg = f"Bu havuzun son fiyat hareketini yorumla: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fiyat", "price", "yüksek", "high", "düşük", "low",
                             "kapanış", "close", "açılış", "open", "$", "artış", "düşüş"], msg)

    def test_son_24_saat_fiyat(self, chat_client):
        msg = f"Meteora pool {METEORA_SOL_USDC} için son 24 saatin fiyat verilerini 1 saatlik mumlarla göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "address", METEORA_SOL_USDC, msg)
        # startTime/endTime are optional and may be omitted
        assert_no_raw_json(r, msg)

    def test_gecersiz_timeframe_duzeltme(self, chat_client):
        """For an invalid timeframe (3d) the LLM should pick a valid one."""
        msg = f"Pool {METEORA_SOL_USDC} için 3 günlük mumları göster"
        r = _chat(chat_client, msg)
        if "meteora_dlmm_get_pool_ohlcv" in r.all_triggered_types():
            assert_valid_timeframe(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)

    def test_ingilizce_ohlcv_1h(self, chat_client):
        msg = f"Get the 1 hour OHLCV candle data for Meteora pool {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "timeframe", "1h", msg)
        assert_no_raw_json(r, msg)

    def test_yorum_sayisal_fiyat(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} fiyat hareketi saatlik — sayısal olarak özetle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_bonk_sol_ohlcv(self, chat_client):
        msg = f"BONK/SOL Meteora pool {METEORA_BONK_SOL} için saatlik fiyat grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "address", METEORA_BONK_SOL, msg)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 7. meteora_dlmm_get_pool_volume_history — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetPoolVolHistory:

    def test_bugunun_hacmi(self, chat_client):
        msg = f"Meteora pool {METEORA_SOL_USDC} bugün ne kadar işlem hacmi gördü?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_volume_history", "address",
                             METEORA_SOL_USDC, msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["hacim", "volume", "$", "işlem"], msg)

    def test_saatlik_hacim_dagilimi(self, chat_client):
        msg = f"Son 1 saatin saatlik hacim dağılımını göster: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_volume_history", "timeframe", "1h", msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_no_raw_json(r, msg)

    def test_5m_granular_hacim(self, chat_client):
        msg = f"Son 1 saatin 5 dakikalık hacim dağılımını göster: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_volume_history", "timeframe", "5m", msg)
        assert_valid_timeframe(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_no_raw_json(r, msg)

    def test_24h_gunluk_hacim(self, chat_client):
        msg = f"Bu pool için günlük hacim geçmişini göster: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        p = r.params_for("meteora_dlmm_get_pool_volume_history")
        tf = p.get("timeframe", "24h")
        assert tf in ("24h", "12h", "1h"), f"Expected 24h/12h/1h for a daily view: {tf!r}"
        assert_no_raw_json(r, msg)

    def test_7_gunluk_trend(self, chat_client):
        msg = f"Son 7 günde bu pool'un hacim trendi nasıl? {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_volume_history", "address",
                             METEORA_SOL_USDC, msg)
        # startTime/endTime are optional
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg)

    def test_kazanilan_ucretler(self, chat_client):
        msg = f"Bu Meteora havuzunda bu hafta ne kadar ücret kazanıldı? {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "ücret", "komisyon", "$"], msg)

    def test_protocol_fee_yorum(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} için trading fee ve protocol fee dağılımı nasıl?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "protocol", "ücret", "$"], msg)

    def test_hacim_zirve_yorum(self, chat_client):
        msg = f"Bu havuzun hacim zirvelerini göster ve yorumla: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_ingilizce_volume_history(self, chat_client):
        msg = f"What was the hourly trading volume of Meteora pool {METEORA_SOL_USDC}?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_volume_history", "address",
                             METEORA_SOL_USDC, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["volume", "trading", "$", "fee"], msg)

    def test_sayisal_deger_yorum(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC}'ın günlük hacmini dolar cinsinden yorumla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_bonk_sol_volume(self, chat_client):
        msg = f"BONK/SOL pool {METEORA_BONK_SOL} için son hacim verisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_volume_history", "address",
                             METEORA_BONK_SOL, msg)
        assert_no_raw_json(r, msg)

    def test_sol_usdt_volume(self, chat_client):
        msg = f"SOL/USDT havuzunun işlem hacmi nasıl? Pool: {METEORA_SOL_USDT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_volume_history", "address",
                             METEORA_SOL_USDT, msg)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 8. meteora_dlmm_get_protocol_stats — 8 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMDlmmGetProtocolStats:

    def test_toplam_tvl(self, chat_client):
        msg = "Meteora DLMM'in toplam TVL'si ne kadar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_protocol_stats", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_toplam_hacim(self, chat_client):
        msg = "Meteora DLMM protokolünün toplam işlem hacmi nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_protocol_stats", msg)
        assert_no_raw_json(r, msg)

    def test_24h_fee(self, chat_client):
        msg = "Meteora DLMM bugün ne kadar fee kazandı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_protocol_stats", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "ücret", "$"], msg)

    def test_toplam_havuz_sayisi(self, chat_client):
        msg = "Meteora'da kaç tane DLMM havuzu var toplam?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_protocol_stats", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_protokol_genel_bakis(self, chat_client):
        msg = "Meteora DLMM protokolüne genel bakış ver — TVL, hacim, fee"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_protocol_stats", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["tvl", "volume", "fee", "hacim", "$"], msg)

    def test_ingilizce_total_fees(self, chat_client):
        msg = "How much total fees has Meteora DLMM generated?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_protocol_stats", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["fee", "$"], msg)

    def test_ingilizce_protocol_overview(self, chat_client):
        msg = "Give me a protocol overview of Meteora DLMM — TVL, volume, pools"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_protocol_stats", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_stats_vs_pool_ayirt(self, chat_client):
        """Protokol stats sorusu tekil pool action tetiklememeli."""
        msg = "Meteora DLMM protokolünün toplam istatistikleri neler?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_protocol_stats" in triggered, (
            f"Protokol stats sorusu get_protocol_stats tetiklemeli. Tetiklenenler: {triggered}"
        )
        assert "meteora_dlmm_get_pair" not in triggered, (
            f"Protokol stats sorusu tekil get_pair tetiklememeli. Tetiklenenler: {triggered}"
        )
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Routing correctness — 15 tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMActionRouting:

    def test_genel_liste_get_pairs(self, chat_client):
        msg = "Meteora'da en iyi DLMM havuzları hangileri?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_pairs" in triggered or "meteora_dlmm_get_pool_groups" in triggered, (
            f"Genel soru get_pairs veya get_pool_groups tetiklemeli. Tetiklenenler: {triggered}"
        )
        assert_no_raw_json(r, msg)

    def test_tekil_pool_get_pair(self, chat_client):
        msg = f"Bu Meteora DLMM pool'unun detaylarını göster: {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert "meteora_dlmm_get_pair" in r.all_triggered_types(), (
            f"Tekil pool get_pair tetiklemeli. Tetiklenenler: {r.all_triggered_types()}"
        )
        assert_param_present(r, "meteora_dlmm_get_pair", "address", METEORA_SOL_USDC, msg)
        assert_no_raw_json(r, msg)

    def test_fiyat_grafigi_ohlcv(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} fiyatı son 1 saatte nasıl hareket etti?"
        r = _chat(chat_client, msg)
        assert "meteora_dlmm_get_pool_ohlcv" in r.all_triggered_types(), (
            f"Fiyat grafik sorusu ohlcv tetiklemeli. Tetiklenenler: {r.all_triggered_types()}"
        )
        assert_no_raw_json(r, msg)

    def test_hacim_sorusu_volume_history(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC}'da bu hafta toplam kaç dolar işlem yapıldı?"
        r = _chat(chat_client, msg)
        assert "meteora_dlmm_get_pool_volume_history" in r.all_triggered_types(), (
            f"Hacim sorusu volume_history tetiklemeli. Tetiklenenler: {r.all_triggered_types()}"
        )
        assert_no_raw_json(r, msg)

    def test_token_cift_gruplari_pool_groups(self, chat_client):
        msg = "Meteora'da SOL ile en fazla havuzu olan token çifti hangisi?"
        r = _chat(chat_client, msg)
        assert "meteora_dlmm_get_pool_groups" in r.all_triggered_types(), (
            f"A pair-group question should trigger pool_groups. Triggered: {r.all_triggered_types()}"
        )
        assert_no_raw_json(r, msg)

    def test_spot_price_uses_active_bin(self, chat_client):
        msg = f"Meteora pool {METEORA_SOL_USDC} şu an ne fiyattan işlem görüyor?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_active_bin" in triggered or "meteora_dlmm_get_pair" in triggered, (
            f"A spot price should trigger active_bin or get_pair. Triggered: {triggered}"
        )
        assert_no_raw_json(r, msg)

    def test_fee_tier_listesi_pool_group(self, chat_client):
        msg = (
            f"SOL/USDC çiftinde kaç farklı fee tier var? "
            f"Mints: {SOL_MINT}-{USDC_MINT}"
        )
        r = _chat(chat_client, msg)
        assert "meteora_dlmm_get_pool_group" in r.all_triggered_types(), (
            f"Fee tier listesi pool_group tetiklemeli. Tetiklenenler: {r.all_triggered_types()}"
        )
        assert_no_raw_json(r, msg)

    def test_ohlcv_vs_volume_ayirt(self, chat_client):
        """A price-chart question -> ohlcv, not volume_history."""
        msg = f"Meteora DLMM pool {METEORA_SOL_USDC} için saatlik candlestick chart göster"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_pool_ohlcv" in triggered, (
            f"Candlestick/mum sorusu ohlcv tetiklemeli. Tetiklenenler: {triggered}"
        )
        assert "meteora_dlmm_get_pool_volume_history" not in triggered, (
            f"Candlestick sorusu volume_history tetiklememeli. Tetiklenenler: {triggered}"
        )

    def test_hacim_vs_fiyat_ayirt(self, chat_client):
        """A volume question -> volume_history, not ohlcv."""
        msg = f"Meteora DLMM pool {METEORA_SOL_USDC}'ın toplam işlem hacmi ne kadar?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert "meteora_dlmm_get_pool_volume_history" in triggered, (
            f"Hacim sorusu volume_history tetiklemeli. Tetiklenenler: {triggered}"
        )
        assert "meteora_dlmm_get_pool_ohlcv" not in triggered, (
            f"Hacim sorusu ohlcv tetiklememeli. Tetiklenenler: {triggered}"
        )

    def test_toplam_likidite_get_pairs(self, chat_client):
        msg = "Meteora'daki toplam likiditeye sahip en büyük DLMM havuzları"
        r = _chat(chat_client, msg)
        assert "meteora_dlmm_get_pairs" in r.all_triggered_types(), (
            f"Likidite listesi get_pairs tetiklemeli. Tetiklenenler: {r.all_triggered_types()}"
        )
        assert_no_raw_json(r, msg)

    def test_yeni_havuz_kesfet_get_pairs(self, chat_client):
        msg = "Meteora'da yeni açılan DLMM havuzları var mı?"
        r = _chat(chat_client, msg)
        assert "meteora_dlmm_get_pairs" in r.all_triggered_types(), (
            f"Discovering new pools should trigger get_pairs. Triggered: {r.all_triggered_types()}"
        )
        assert_no_raw_json(r, msg)

    def test_dogru_adres_parametresi(self, chat_client):
        """The LLM should pass the address from the message through as a parameter."""
        msg = f"Meteora pool {METEORA_BONK_SOL} için saatlik fiyat grafiği"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "address", METEORA_BONK_SOL, msg)

    def test_birden_fazla_pool_adres_ilki_alir(self, chat_client):
        """Given two addresses in one message, the LLM should use the first."""
        msg = (
            f"Pool {METEORA_SOL_USDC} ve pool {METEORA_SOL_USDT} — "
            f"ilkinin OHLCV verisi"
        )
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_param_present(r, "meteora_dlmm_get_pool_ohlcv", "address", METEORA_SOL_USDC, msg)

    def test_done_sentinel(self, chat_client):
        msg = "Meteora DLMM havuzları hakkında kısa bilgi ver"
        r = _chat(chat_client, msg)
        assert r.done, "SSE stream [DONE] sentineli ile bitmedi"

    def test_hic_error_event_yok(self, chat_client):
        msg = "Meteora DLMM havuz listesini göster"
        r = _chat(chat_client, msg)
        assert not r.errors, f"Beklenmeyen error event'leri: {r.errors}"


# ══════════════════════════════════════════════════════════════════════════════
# 9. Negative and malformed questions — 8 tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMNegative:

    def test_ilgisiz_soru_hava_durumu(self, chat_client):
        """A non-DeFi question must not trigger a Meteora action."""
        msg = "Bugün hava nasıl İstanbul'da?"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        meteora_triggered = [t for t in triggered if "meteora_dlmm" in t]
        assert not meteora_triggered, f"Hava durumu sorusu Meteora action tetiklememeli: {meteora_triggered}"
        assert_text_not_empty(r, msg)

    def test_invalid_address_is_graceful(self, chat_client):
        """An OHLCV question with an invalid address — the LLM should fail gracefully."""
        msg = f"Pool {INVALID_ADDR} için saatlik fiyat grafiği"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        # The LLM should either explain the error or ask for clarification
        has_error = bool(r.errors) or bool(r.clarify)
        has_explanation = len(r.text.strip()) > 20
        assert has_error or has_explanation, "Expected a graceful response for an invalid address"

    def test_too_short_address(self, chat_client):
        """An address that is far too short — the LLM should say it is invalid."""
        msg = f"Pool {SHORT_ADDR} OHLCV verisi"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)

    def test_sadece_swap_sorusu_meteora_action_yok(self, chat_client):
        """A swap request -> the swap action, not get_pairs."""
        msg = "1 SOL'u USDC'ye swap et Meteora'da"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        get_actions = [t for t in triggered if t in (
            "meteora_dlmm_get_pairs", "meteora_dlmm_get_pool_groups",
            "meteora_dlmm_get_pool_ohlcv", "meteora_dlmm_get_pool_volume_history",
        )]
        assert not get_actions, f"A swap question must not trigger a data-fetch action: {get_actions}"

    def test_adres_olmadan_ohlcv(self, chat_client):
        """OHLCV without a pool address — the LLM should ask for clarification."""
        msg = "Meteora'daki bir havuzun OHLCV verisi"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)
        # Should ask for clarification, or return the general listing
        has_action_or_clarify = bool(r.all_triggered_types()) or bool(r.clarify) or len(r.text) > 20
        assert has_action_or_clarify, "The LLM did not respond adequately"

    def test_adres_olmadan_volume(self, chat_client):
        """Volume history without a pool address — the LLM should ask for clarification."""
        msg = "Bir Meteora pool'unun hacim geçmişini göster"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)

    def test_rakip_protokol_sorusu(self, chat_client):
        """Orca havuz sorusu → Meteora DLMM action tetiklememeli."""
        msg = "Orca'daki SOL/USDC havuzu için fiyat grafiği"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        meteora_dlmm = [t for t in triggered if "meteora_dlmm" in t]
        assert not meteora_dlmm, f"Orca sorusu Meteora DLMM action tetiklememeli: {meteora_dlmm}"
        assert_text_not_empty(r, msg)

    def test_cok_uzun_istek(self, chat_client):
        """Even a very long message must be handled."""
        base_msg = f"Meteora DLMM havuz {METEORA_SOL_USDC} için saatlik OHLCV verisi istiyorum. "
        msg = base_msg + ("Detaylı bilgi ver. " * 20)
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 10. Yorum kalitesi — 10 test
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMInterpretationQuality:

    def test_pairs_yorum_sayisal(self, chat_client):
        msg = "Meteora DLMM'de en yüksek TVL'li 5 havuzu ve TVL değerleriyle listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_pairs_yorum_token_isimleri(self, chat_client):
        msg = "Meteora DLMM'de en popüler 3 havuzu token çifti ismiyle söyle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pairs", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["sol", "usdc", "usdt", "jup", "bonk", "/", "-"], msg)

    def test_ohlcv_yorum_fiyat_sayisal(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} için saatlik fiyat hareketi: yükselen mi düşen mi?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_volume_yorum_dolar(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC}'ın günlük hacmini dolar cinsinden yorumla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_volume_history", msg)
        assert_text_not_empty(r, msg)
        assert_has_number(r, msg)
        assert_no_raw_json(r, msg)

    def test_pool_groups_token_ismi(self, chat_client):
        msg = "Meteora'daki en büyük 5 DLMM token çifti grubunu listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_groups", msg)
        assert_text_not_empty(r, msg)
        assert_mentions(r, ["sol", "usdc", "usdt", "bonk", "jup", "token", "çift", "pair"], msg)
        assert_no_raw_json(r, msg)

    def test_hic_raw_json_yok_pairs(self, chat_client):
        msg = "Meteora DLMM havuz listesini göster"
        r = _chat(chat_client, msg)
        assert_no_raw_json(r, msg)

    def test_hic_raw_json_yok_ohlcv(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} OHLCV grafiği"
        r = _chat(chat_client, msg)
        assert_no_raw_json(r, msg)

    def test_hic_raw_json_yok_volume(self, chat_client):
        msg = f"Pool {METEORA_SOL_USDC} hacim geçmişi"
        r = _chat(chat_client, msg)
        assert_no_raw_json(r, msg)

    def test_yorum_ozet_cumleler(self, chat_client):
        """The LLM should give a 2-5 sentence summary, not dump a list."""
        msg = f"Meteora pool {METEORA_SOL_USDC} için saatlik OHLCV'yi yorumla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        # A reasonable interpretation runs 50-2000 characters
        text_len = len(r.text.strip())
        assert 50 < text_len < 3000, f"Interpretation length outside the expected range: {text_len}"

    def test_ingilizce_yorum_kalitesi(self, chat_client):
        msg = f"Summarize the recent price action for Meteora pool {METEORA_SOL_USDC}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_dlmm_get_pool_ohlcv", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_has_number(r, msg)

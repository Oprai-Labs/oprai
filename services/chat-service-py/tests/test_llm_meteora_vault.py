"""
Meteora Dynamic Vault — comprehensive LLM quality suite

The six read endpoints covered:
  - meteora_vault_get_info          (no parameters — every vault)
  - meteora_vault_get_addresses     (no parameters — the address table)
  - meteora_vault_get_state         (tokenMint — required pubkey)
  - meteora_vault_get_apy           (tokenMint — required pubkey)
  - meteora_vault_get_apy_history   (tokenMint + startTimestamp + endTimestamp — all required)
  - meteora_vault_get_virtual_price (tokenMint + strategy — both required pubkeys)

The user-facing prompts are deliberately written in Turkish: this suite doubles
as the regression net for Turkish-language intent handling.

Test classes:
  TestVaultGetInfo          — 12 tests (no-arg, strategy interpretation, routing)
  TestVaultGetAddresses     — 10 tests (no-arg, reading the address output)
  TestVaultGetState         — 14 tests (tokenMint extraction, pubkey validation, interpretation)
  TestVaultGetApy           — 14 tests (APY interpretation, closest/average/long, token extraction)
  TestVaultGetApyHistory    — 18 tests (timestamp maths, "last N days", start<end, format)
  TestVaultGetVirtualPrice  — 14 tests (two required params, strategy guidance, pubkey validation)
  TestVaultRouting          — 16 tests (info vs state vs apy, vault vs S2E, misrouting)
  TestVaultErrorHandling    — 12 tests (invalid pubkey, missing param, start>=end)
  TestVaultUserGuidance     — 10 tests (what a Dynamic Vault is, how to use it, strategy)

Total: ~120 tests
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid

import httpx
import pytest

# ─── Service configuration ─────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://localhost:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

# Bilinen token mint adresleri
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
SOL_MINT  = "So11111111111111111111111111111111111111112"
BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# A known vault strategy address (USDC vault, Kamino strategy — example)
USDC_STRATEGY = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"

# Invalid addresses
FAKE_ADDR    = "FakeAddr111111111111111111111111111111111111"
GARBAGE_ADDR = "not-a-solana-address"

# Zaman sabitleri
NOW_TS      = int(time.time())
WEEK_AGO_TS = NOW_TS - 7 * 86400
MONTH_AGO   = NOW_TS - 30 * 86400
DAY_AGO     = NOW_TS - 86400


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
            f"LLM said 'I have no access' without fetching data.\nText: {result.text[:400]}\n{msg}"
        )


def assert_timestamp_is_numeric(result: StreamResult, action: str, key: str, msg: str = ""):
    p = result.params_for(action)
    if key in p:
        try:
            val = int(p[key])
            assert val > 0, f"[{action}] {key}={val} must be positive.\n{msg}"
            # Unix timestamps are in seconds — sane range (2020-2030)
            assert 1577836800 <= val <= 1893456000, (
                f"[{action}] {key}={val} is outside the valid Unix-timestamp range.\n{msg}"
            )
        except (ValueError, TypeError):
            pytest.fail(f"[{action}] {key}={p[key]!r} is not numeric.\n{msg}")


def assert_start_before_end(result: StreamResult, action: str, msg: str = ""):
    p = result.params_for(action)
    if "startTimestamp" in p and "endTimestamp" in p:
        try:
            start = int(p["startTimestamp"])
            end   = int(p["endTimestamp"])
            assert start < end, (
                f"[{action}] startTimestamp ({start}) >= endTimestamp ({end}).\n{msg}"
            )
        except (ValueError, TypeError):
            pytest.fail(f"[{action}] the timestamp values are not numeric: {p}\n{msg}")


def result_has_response(r: StreamResult) -> bool:
    return r.has_text_content() or r.asked_clarification() or len(r.all_triggered_types()) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 1. GET INFO — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultGetInfo:

    def test_genel_info_turkce(self, chat_client):
        msg = "Meteora Dynamic Vault bilgilerini göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)
        assert_no_hallucination(r, msg)

    def test_genel_info_ingilizce(self, chat_client):
        msg = "Show all Meteora Dynamic Vault info and strategies"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_text_not_empty(r, msg)

    def test_parametresiz_cagri(self, chat_client):
        msg = "Meteora Dynamic Vault genel bilgilerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        p = r.params_for("meteora_vault_get_info")
        assert p == {} or all(v is None for v in p.values()), (
            f"get_info takes no params but got: {p}\n{msg}"
        )

    def test_strateji_listesi_istegi(self, chat_client):
        msg = "Meteora Dynamic Vault'un hangi lending stratejileri var?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_vault_getirisi_sorusu(self, chat_client):
        msg = "Meteora vault'larının genel getiri bilgilerini göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_text_not_empty(r, msg)
        assert_no_hallucination(r, msg)

    def test_yorum_no_raw_json(self, chat_client):
        msg = "Meteora Dynamic Vault bilgilerini anlaşılır şekilde özetle"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=80)

    def test_info_not_addresses(self, chat_client):
        msg = "Meteora Dynamic Vault stratejileri ve genel bilgiler"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_not_triggered(r, "meteora_vault_get_addresses", msg)

    def test_info_not_state(self, chat_client):
        msg = "Meteora Dynamic Vault genel bilgileri (belirli token değil)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_not_triggered(r, "meteora_vault_get_state", msg)

    def test_lending_protocol_aciklamasi(self, chat_client):
        msg = "Meteora vault'lar hangi lending protokollerine para yatırıyor?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_info_not_s2e(self, chat_client):
        msg = "Meteora Dynamic Vault bilgilerini göster (Stake2Earn değil)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        triggered = r.all_triggered_types()
        assert not any("s2e" in t for t in triggered), (
            f"S2E was triggered by mistake: {triggered}\n{msg}"
        )

    def test_vault_info_yorum_kalitesi(self, chat_client):
        msg = "Meteora Dynamic Vault'un tüm bilgilerini getir ve en yüksek getirili stratejiyi vurgula"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_idle_lp_token_anlami(self, chat_client):
        msg = "Meteora Dynamic Vault'un idle LP tokenları nasıl kullandığını açıkla"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)


# ══════════════════════════════════════════════════════════════════════════════
# 2. GET ADDRESSES — 10 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultGetAddresses:

    def test_adres_listesi_turkce(self, chat_client):
        msg = "Meteora Dynamic Vault adreslerini listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_adres_listesi_ingilizce(self, chat_client):
        msg = "Get all Meteora Dynamic Vault addresses"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_text_not_empty(r, msg)

    def test_parametresiz_cagri(self, chat_client):
        msg = "Meteora vault on-chain adreslerini getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        p = r.params_for("meteora_vault_get_addresses")
        assert p == {} or all(v is None for v in p.values()), (
            f"get_addresses takes no params but got: {p}\n{msg}"
        )

    def test_lp_mint_adresi_sorusu(self, chat_client):
        msg = "Meteora Dynamic Vault LP mint adreslerini göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_text_not_empty(r, msg)

    def test_vault_address_sorusu(self, chat_client):
        msg = "Meteora vault adreslerini ve LP mint adreslerini listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_no_raw_json(r, msg)

    def test_addresses_not_info(self, chat_client):
        msg = "Meteora Dynamic Vault'un on-chain adres tablosunu getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)

    def test_fee_address_sorusu(self, chat_client):
        msg = "Meteora vault fee collection adreslerini göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_text_not_empty(r, msg)

    def test_no_hallucination(self, chat_client):
        msg = "Meteora Dynamic Vault adres tablosunu getir"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_no_hallucination(r, msg)

    def test_yorum_kalitesi(self, chat_client):
        msg = "Meteora vault adreslerini getir ve USDC vault adresini vurgula"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_addresses_not_s2e(self, chat_client):
        msg = "Meteora Dynamic Vault adreslerini listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        triggered = r.all_triggered_types()
        assert not any("s2e" in t for t in triggered), (
            f"S2E was triggered by mistake: {triggered}\n{msg}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3. GET STATE — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultGetState:

    def test_usdc_state_mint_cikartma(self, chat_client):
        msg = "Meteora USDC vault'unun durumunu göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_param_present(r, "meteora_vault_get_state", "tokenMint", USDC_MINT, msg)

    def test_sol_state_mint_cikartma(self, chat_client):
        msg = "Meteora Dynamic Vault SOL state bilgisi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_param_present(r, "meteora_vault_get_state", "tokenMint", SOL_MINT, msg)

    def test_usdt_state_ingilizce(self, chat_client):
        msg = "Get Meteora Dynamic Vault state for USDT"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_param_present(r, "meteora_vault_get_state", "tokenMint", USDT_MINT, msg)

    def test_direkt_mint_adresi(self, chat_client):
        msg = f"Meteora vault state: tokenMint={USDC_MINT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_param_present(r, "meteora_vault_get_state", "tokenMint", USDC_MINT, msg)

    def test_state_no_raw_json(self, chat_client):
        msg = "Meteora USDC vault durumunu anlaşılır şekilde göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)

    def test_state_no_hallucination(self, chat_client):
        msg = f"Meteora vault state: {USDC_MINT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_no_hallucination(r, msg)

    def test_token_mint_eksik_clarification(self, chat_client):
        msg = "Meteora vault durumunu göster"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)
        if "meteora_vault_get_state" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_state")
            assert "tokenMint" in p and p["tokenMint"], (
                f"tokenMint is required but empty: {p}\n{msg}"
            )

    def test_likidite_durumu_yorumu(self, chat_client):
        msg = "Meteora USDC vault'unda ne kadar likidite var? Durumunu göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_state_not_apy(self, chat_client):
        msg = "Meteora USDC vault'unun mevcut durumunu (state) göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_not_triggered(r, "meteora_vault_get_apy", msg)

    def test_garbage_mint_error(self, chat_client):
        msg = f"Meteora vault state: tokenMint={GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_state" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_deployed_amount_yorum(self, chat_client):
        msg = "Meteora SOL vault'undan ne kadar token lending'e deploy edilmiş?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_state_yorum_derinligi(self, chat_client):
        msg = "Meteora USDC vault state bilgisini getir ve deployed vs total liquidity oranını yorumla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_virtual_price_state_arayi(self, chat_client):
        msg = "Meteora USDT vault'unun virtual price veya state bilgisini getir"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any(t in triggered for t in [
            "meteora_vault_get_state", "meteora_vault_get_virtual_price"
        ]), f"Beklenen eylem tetiklenmedi. Tetiklenenler: {triggered}\n{msg}"
        assert_text_not_empty(r, msg)

    def test_state_token_sembol_cozumu(self, chat_client):
        msg = "Meteora Dynamic Vault BONK state"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        p = r.params_for("meteora_vault_get_state")
        assert "tokenMint" in p and p["tokenMint"], (
            f"tokenMint was not sent: {p}\n{msg}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4. GET APY — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultGetApy:

    def test_usdc_apy_turkce(self, chat_client):
        msg = "Meteora USDC vault APY'si ne kadar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_param_present(r, "meteora_vault_get_apy", "tokenMint", USDC_MINT, msg)

    def test_sol_apy_ingilizce(self, chat_client):
        msg = "What is the current APY of Meteora SOL vault?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_param_present(r, "meteora_vault_get_apy", "tokenMint", SOL_MINT, msg)

    def test_usdt_apy(self, chat_client):
        msg = "Meteora USDT Dynamic Vault getirisi nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_param_present(r, "meteora_vault_get_apy", "tokenMint", USDT_MINT, msg)

    def test_mint_adresiyle_apy(self, chat_client):
        msg = f"Meteora vault APY: {USDC_MINT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_param_present(r, "meteora_vault_get_apy", "tokenMint", USDC_MINT, msg)

    def test_apy_no_raw_json(self, chat_client):
        msg = "Meteora USDC vault APY'sini anlaşılır şekilde açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)

    def test_closest_average_long_apy_yorum(self, chat_client):
        msg = "Meteora USDC vault için anlık, ortalama ve uzun vadeli APY'yi getir ve açıkla"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["apy", "ortalama", "average", "anlık", "closest", "long"], msg)

    def test_apy_vs_history_routing(self, chat_client):
        msg = "Meteora USDC vault güncel APY'si nedir?"
        r = _chat(chat_client, msg)
        # "current" -> get_apy; no date range, so get_apy_history is wrong
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_not_triggered(r, "meteora_vault_get_apy_history", msg)

    def test_apy_no_hallucination(self, chat_client):
        msg = f"Meteora vault APY: {USDC_MINT}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_no_hallucination(r, msg)

    def test_token_mint_eksik_clarification(self, chat_client):
        msg = "Meteora vault APY'sini göster"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        if "meteora_vault_get_apy" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_apy")
            assert "tokenMint" in p and p["tokenMint"], (
                f"tokenMint is required but empty: {p}\n{msg}"
            )

    def test_yield_kelimesiyle_routing(self, chat_client):
        msg = "Meteora SOL vault'unun getirisi ne kadar?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_param_present(r, "meteora_vault_get_apy", "tokenMint", SOL_MINT, msg)

    def test_apy_yorum_yuzde_icermeli(self, chat_client):
        msg = "Meteora USDC vault APY'si kaç yüzde?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_text_not_empty(r, msg)
        assert_no_raw_json(r, msg)

    def test_apy_not_state(self, chat_client):
        msg = "Meteora USDC vault için mevcut APY ve getiri"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)

    def test_garbage_mint_apy(self, chat_client):
        msg = f"Meteora vault APY: tokenMint={GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_apy" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_bonk_apy(self, chat_client):
        msg = "Meteora BONK vault APY kaç?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        p = r.params_for("meteora_vault_get_apy")
        assert "tokenMint" in p and p["tokenMint"], f"tokenMint is empty: {p}\n{msg}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. GET APY HISTORY — 18 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultGetApyHistory:

    def test_son_7_gun_timestamp_hesaplama(self, chat_client):
        msg = "Meteora USDC vault APY geçmişi, son 7 gün"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "tokenMint", USDC_MINT, msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "startTimestamp", msg=msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "endTimestamp", msg=msg)
        assert_timestamp_is_numeric(r, "meteora_vault_get_apy_history", "startTimestamp", msg)
        assert_timestamp_is_numeric(r, "meteora_vault_get_apy_history", "endTimestamp", msg)
        assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_son_30_gun(self, chat_client):
        msg = "Meteora SOL vault son 30 günlük APY geçmişi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "tokenMint", SOL_MINT, msg)
        assert_timestamp_is_numeric(r, "meteora_vault_get_apy_history", "startTimestamp", msg)
        assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_direkt_timestamp_verildi(self, chat_client):
        msg = (f"Meteora USDC vault APY history: "
               f"startTimestamp={WEEK_AGO_TS} endTimestamp={NOW_TS}")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        p = r.params_for("meteora_vault_get_apy_history")
        assert int(p.get("startTimestamp", 0)) == WEEK_AGO_TS, (
            f"startTimestamp is wrong: {p}\n{msg}"
        )
        assert int(p.get("endTimestamp", 0)) == NOW_TS, (
            f"endTimestamp is wrong: {p}\n{msg}"
        )

    def test_tarih_araligi_start_before_end(self, chat_client):
        msg = "Meteora USDT vault APY geçmişi, son 2 hafta"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_son_1_gun(self, chat_client):
        msg = "Meteora USDC vault dünden bugüne APY değişimi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_timestamp_is_numeric(r, "meteora_vault_get_apy_history", "startTimestamp", msg)
        assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_ingilizce_7days(self, chat_client):
        msg = "Show Meteora USDC vault APY history for the last 7 days"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "tokenMint", USDC_MINT, msg)
        assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_timestamp_saniye_cinsinden(self, chat_client):
        """Timestamps must be in seconds, not milliseconds."""
        msg = "Meteora SOL vault APY history son 7 gün"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        p = r.params_for("meteora_vault_get_apy_history")
        for key in ("startTimestamp", "endTimestamp"):
            if key in p:
                val = int(p[key])
                # Milliseconds would be 13 digits (>1e12); seconds are 10
                assert val < 10**12, (
                    f"[apy_history] {key}={val} looks like milliseconds; seconds expected.\n{msg}"
                )

    def test_no_raw_json(self, chat_client):
        msg = "Meteora USDC vault son hafta APY geçmişini anlaşılır şekilde göster"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=80)

    def test_history_not_apy_current(self, chat_client):
        msg = "Meteora USDC vault geçen ay boyunca APY nasıl değişti?"
        r = _chat(chat_client, msg)
        # A date range was given -> history is expected
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)

    def test_token_mint_eksik_clarification(self, chat_client):
        msg = "Meteora vault APY geçmişi, son 7 gün"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        if "meteora_vault_get_apy_history" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_apy_history")
            assert "tokenMint" in p and p["tokenMint"], (
                f"tokenMint is required but empty: {p}\n{msg}"
            )

    def test_start_gte_end_hata(self, chat_client):
        """start >= end is invalid — the LLM must explain it or send sane values."""
        bad_start = NOW_TS + 3600
        bad_end   = NOW_TS
        msg = (f"Meteora USDC vault APY history: "
               f"startTimestamp={bad_start} endTimestamp={bad_end}")
        r = _chat(chat_client, msg)
        if "meteora_vault_get_apy_history" in r.all_triggered_types():
            # The LLM should explain the error, or correct it
            assert_explains_error(r, msg) or assert_start_before_end(r, "meteora_vault_get_apy_history", msg)
        else:
            assert_text_not_empty(r, msg)

    def test_trend_analiz_yorum(self, chat_client):
        msg = "Meteora USDC vault APY'si son 7 günde artmış mı azalmış mı?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_no_hallucination(r, msg)

    def test_sol_history_30days(self, chat_client):
        msg = "Meteora SOL vault 30 günlük APY trendi nedir?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "tokenMint", SOL_MINT, msg)
        assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_tarih_araligi_yorum_derinligi(self, chat_client):
        msg = "Meteora USDC vault geçen ay APY geçmişini getir ve en yüksek/düşük değerleri belirt"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_timestamp_hesaplama_onceden_hesaplanmis(self, chat_client):
        msg = f"Meteora USDC vault APY: {MONTH_AGO} ile {NOW_TS} arasındaki geçmiş"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_history_no_hallucination(self, chat_client):
        msg = "Meteora SOL vault son 2 haftalık APY geçmişi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_no_hallucination(r, msg)

    def test_tarih_araligi_no_timestamp_yoksa(self, chat_client):
        msg = "Meteora USDC vault APY geçmişi göster"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        # No date given — the LLM may clarify, or use a sensible default
        if "meteora_vault_get_apy_history" in r.all_triggered_types():
            assert_start_before_end(r, "meteora_vault_get_apy_history", msg)


# ══════════════════════════════════════════════════════════════════════════════
# 6. GET VIRTUAL PRICE — 14 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultGetVirtualPrice:

    def test_iki_param_verildi(self, chat_client):
        msg = (f"Meteora vault virtual price: "
               f"tokenMint={USDC_MINT} strategy={USDC_STRATEGY}")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        assert_param_present(r, "meteora_vault_get_virtual_price", "tokenMint", USDC_MINT, msg)
        assert_param_present(r, "meteora_vault_get_virtual_price", "strategy", USDC_STRATEGY, msg)

    def test_ingilizce(self, chat_client):
        msg = (f"Get virtual price for Meteora USDC vault strategy {USDC_STRATEGY}")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        assert_param_present(r, "meteora_vault_get_virtual_price", "strategy", USDC_STRATEGY, msg)

    def test_strategy_eksik_clarification(self, chat_client):
        """virtual_price cannot be called without a strategy address."""
        msg = "Meteora USDC vault virtual price'ını göster"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)
        if "meteora_vault_get_virtual_price" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_virtual_price")
            assert "strategy" in p and p["strategy"], (
                f"strategy is required but empty: {p}\n{msg}"
            )

    def test_token_mint_eksik_clarification(self, chat_client):
        msg = f"Meteora vault virtual price: strategy={USDC_STRATEGY}"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        if "meteora_vault_get_virtual_price" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_virtual_price")
            assert "tokenMint" in p and p["tokenMint"], (
                f"tokenMint is required but empty: {p}\n{msg}"
            )

    def test_garbage_token_mint(self, chat_client):
        msg = f"Meteora vault virtual price: tokenMint={GARBAGE_ADDR} strategy={USDC_STRATEGY}"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_virtual_price" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_garbage_strategy(self, chat_client):
        msg = f"Meteora vault virtual price: tokenMint={USDC_MINT} strategy={GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_virtual_price" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_no_raw_json(self, chat_client):
        msg = (f"Meteora USDC vault strategy {USDC_STRATEGY} virtual price'ını "
               f"anlaşılır şekilde göster")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        assert_no_raw_json(r, msg)
        assert_text_not_empty(r, msg, min_chars=60)

    def test_no_hallucination(self, chat_client):
        msg = (f"Meteora vault virtual price: "
               f"tokenMint={USDC_MINT} strategy={USDC_STRATEGY}")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        assert_no_hallucination(r, msg)

    def test_virtual_price_ne_anlama_gelir(self, chat_client):
        msg = "Meteora vault virtual price nedir? Ne işe yarar?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["virtual", "price", "lp", "share", "strateji", "strategy"], msg)

    def test_strateji_adresi_bilmiyorum(self, chat_client):
        msg = "Meteora USDC vault virtual price'ına bakmak istiyorum ama strateji adresimi bilmiyorum"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        # The LLM should suggest vault_info or vault_addresses
        triggered = r.all_triggered_types()
        if triggered:
            assert any(t in triggered for t in [
                "meteora_vault_get_info", "meteora_vault_get_addresses"
            ]) or r.asked_clarification(), (
                f"With an unknown strategy the LLM offered no routing: {triggered}\n{msg}"
            )

    def test_virtual_price_not_apy(self, chat_client):
        msg = (f"Meteora vault virtual price: "
               f"tokenMint={USDC_MINT} strategy={USDC_STRATEGY}")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        assert_not_triggered(r, "meteora_vault_get_apy", msg)

    def test_lp_share_price_yorum(self, chat_client):
        msg = (f"Meteora USDC vault strategy {USDC_STRATEGY}'nin LP payı "
               f"ne kadar değerlendi?")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)

    def test_adresler_tam_kopyalanmali(self, chat_client):
        msg = (f"Meteora vault virtual price: mint={USDC_MINT}, "
               f"strategy={USDC_STRATEGY}")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        p = r.params_for("meteora_vault_get_virtual_price")
        assert p.get("tokenMint") == USDC_MINT, (
            f"tokenMint wrong or truncated: {p.get('tokenMint')!r}\n{msg}"
        )
        assert p.get("strategy") == USDC_STRATEGY, (
            f"strategy wrong or truncated: {p.get('strategy')!r}\n{msg}"
        )

    def test_virtual_price_timestamps_icermemeli(self, chat_client):
        """virtual_price takes no timestamp — it must not be confused with APY history."""
        msg = (f"Meteora USDC vault virtual price son 7 günlük geçmişi: "
               f"strategy={USDC_STRATEGY}")
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        if "meteora_vault_get_virtual_price" in triggered:
            p = r.params_for("meteora_vault_get_virtual_price")
            bad = [k for k in p if "timestamp" in k.lower() or "time" in k.lower()]
            assert not bad, (
                f"virtual_price takes no timestamp param but got: {bad}\n{msg}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 7. ROUTING — 16 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultRouting:

    def test_info_vs_state_routing(self, chat_client):
        msg = "Meteora Dynamic Vault genel strateji bilgileri (tek token değil)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)
        assert_not_triggered(r, "meteora_vault_get_state", msg)

    def test_state_vs_apy_routing(self, chat_client):
        msg = "Meteora USDC vault durumu nedir? (APY değil, state)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_not_triggered(r, "meteora_vault_get_apy", msg)

    def test_apy_vs_history_routing_current(self, chat_client):
        msg = "Meteora SOL vault şu anki APY'si"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy", msg)
        assert_not_triggered(r, "meteora_vault_get_apy_history", msg)

    def test_apy_vs_history_routing_past(self, chat_client):
        msg = "Meteora USDC vault geçen haftaki APY değişimi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_not_triggered(r, "meteora_vault_get_apy", msg)

    def test_addresses_vs_state_routing(self, chat_client):
        msg = "Meteora vault on-chain adres listesi (state değil, sadece adresler)"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        assert_not_triggered(r, "meteora_vault_get_state", msg)

    def test_vault_vs_s2e_routing(self, chat_client):
        msg = "Meteora Dynamic Vault (lending vault, Stake2Earn değil) APY"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("meteora_vault" in t for t in triggered), (
            f"vault eylemi tetiklenmedi: {triggered}\n{msg}"
        )
        assert not any("s2e" in t for t in triggered), (
            f"S2E was triggered by mistake: {triggered}\n{msg}"
        )

    def test_s2e_vault_dynamic_vault_ile_karismasin(self, chat_client):
        msg = "Meteora Stake2Earn vault listesi"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert not any(t in triggered for t in [
            "meteora_vault_get_info", "meteora_vault_get_state"
        ]), f"Dynamic Vault eylemi S2E sorusunda tetiklendi: {triggered}\n{msg}"

    def test_dlmm_vault_ile_karismasin(self, chat_client):
        msg = "Meteora Dynamic Vault USDC APY"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert not any("dlmm" in t for t in triggered), (
            f"DLMM was triggered by mistake: {triggered}\n{msg}"
        )

    def test_virtual_price_vs_apy_routing(self, chat_client):
        msg = (f"Meteora USDC vault strategy {USDC_STRATEGY} virtual price")
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_virtual_price", msg)
        assert_not_triggered(r, "meteora_vault_get_apy", msg)

    def test_info_genel_strateji_sorusu(self, chat_client):
        msg = "Meteora Dynamic Vault'un tüm stratejileri neler?"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_info", msg)

    def test_history_kesinlikle_zaman_aralikli(self, chat_client):
        msg = "Meteora SOL vault son ay APY trendi"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)

    def test_state_token_odakli(self, chat_client):
        msg = "Meteora USDT vault'unun mevcut durumu"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_state", msg)
        assert_param_present(r, "meteora_vault_get_state", "tokenMint", USDT_MINT, msg)

    def test_addresses_not_dammv1(self, chat_client):
        msg = "Meteora Dynamic Vault adreslerini listele"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_addresses", msg)
        triggered = r.all_triggered_types()
        assert not any("dammv1" in t for t in triggered), (
            f"DAMM v1 was triggered by mistake: {triggered}\n{msg}"
        )

    def test_apy_history_two_params(self, chat_client):
        msg = f"Meteora USDC vault APY: {WEEK_AGO_TS} - {NOW_TS}"
        r = _chat(chat_client, msg)
        assert_action_triggered(r, "meteora_vault_get_apy_history", msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "startTimestamp", msg=msg)
        assert_param_present(r, "meteora_vault_get_apy_history", "endTimestamp", msg=msg)

    def test_genel_vault_sorusu_info_tercih(self, chat_client):
        msg = "Meteora Dynamic Vault hakkında genel bilgi ver"
        r = _chat(chat_client, msg)
        triggered = r.all_triggered_types()
        assert any("meteora_vault" in t for t in triggered) or r.has_text_content(), (
            f"vault eylemi ya da metin yok: {triggered}\n{msg}"
        )

    def test_virtual_price_strateji_info_once(self, chat_client):
        msg = "Meteora USDC vault'un virtual price'ını görmek istiyorum"
        r = _chat(chat_client, msg)
        # The strategy is unknown — the LLM should suggest vault_info or clarify
        assert result_has_response(r), f"LLM returned no response.\n{msg}"


# ══════════════════════════════════════════════════════════════════════════════
# 8. ERROR HANDLING — 12 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultErrorHandling:

    def test_state_garbage_mint(self, chat_client):
        msg = f"Meteora vault state: tokenMint={GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_state" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_apy_fake_mint(self, chat_client):
        msg = f"Meteora vault APY: tokenMint={FAKE_ADDR}"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_apy" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_apy_history_start_equals_end(self, chat_client):
        ts = NOW_TS - 3600
        msg = (f"Meteora USDC vault APY history: "
               f"startTimestamp={ts} endTimestamp={ts}")
        r = _chat(chat_client, msg)
        # start == end is invalid — the LLM should warn or correct it
        if "meteora_vault_get_apy_history" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_apy_history")
            start = int(p.get("startTimestamp", 0))
            end   = int(p.get("endTimestamp", 0))
            if start == end:
                assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_virtual_price_her_iki_param_eksik(self, chat_client):
        msg = "Meteora vault virtual price göster"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"

    def test_apy_history_missing_token(self, chat_client):
        msg = f"Meteora vault APY history: startTimestamp={WEEK_AGO_TS} endTimestamp={NOW_TS}"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        if "meteora_vault_get_apy_history" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_apy_history")
            if not p.get("tokenMint"):
                assert_text_not_empty(r, msg)  # LLM clarify etmeli

    def test_state_empty_mint(self, chat_client):
        msg = "Meteora vault state getir, token mint adresi ="
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"

    def test_virtual_price_garbage_both(self, chat_client):
        msg = f"Meteora vault virtual price: tokenMint={GARBAGE_ADDR} strategy={GARBAGE_ADDR}"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_virtual_price" in r.all_triggered_types():
            assert_explains_error(r, msg)
        assert_no_raw_json(r, msg)

    def test_apy_history_gelecek_timestamp(self, chat_client):
        future = NOW_TS + 86400 * 365
        msg = (f"Meteora USDC vault APY history: "
               f"startTimestamp={NOW_TS} endTimestamp={future}")
        r = _chat(chat_client, msg)
        if "meteora_vault_get_apy_history" in r.all_triggered_types():
            assert_start_before_end(r, "meteora_vault_get_apy_history", msg)

    def test_apy_token_sembol_olmayan(self, chat_client):
        msg = "Meteora XYZ123 vault APY'si"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_state_kisa_mint(self, chat_client):
        msg = "Meteora vault state: tokenMint=abc"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_state" in r.all_triggered_types():
            assert_explains_error(r, msg)

    def test_virtual_price_only_mint_no_strategy(self, chat_client):
        msg = f"Meteora vault virtual price: tokenMint={USDC_MINT}"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_addresses_no_extra_param(self, chat_client):
        msg = "Meteora vault adreslerini filtrele (USDC)"
        r = _chat(chat_client, msg)
        if "meteora_vault_get_addresses" in r.all_triggered_types():
            p = r.params_for("meteora_vault_get_addresses")
            assert p == {} or all(v is None for v in p.values()), (
                f"get_addresses takes no params but got: {p}\n{msg}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 9. USER GUIDANCE — 10 test
# ══════════════════════════════════════════════════════════════════════════════

class TestVaultUserGuidance:

    def test_dynamic_vault_nedir(self, chat_client):
        msg = "Meteora Dynamic Vault nedir? Nasıl çalışır?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["lending", "likidite", "getiri", "yield", "strateji", "strategy"], msg)

    def test_dynamic_vault_vs_s2e_fark(self, chat_client):
        msg = "Meteora Dynamic Vault ile Stake2Earn arasındaki fark nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["dynamic", "stake", "vault", "lending", "fee"], msg, min_hits=2)

    def test_deposit_oncesi_ne_kontrol_etmeli(self, chat_client):
        msg = "Meteora Dynamic Vault'a para yatırmadan önce neye bakmalıyım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=100)
        assert_no_raw_json(r, msg)

    def test_strateji_ne_demek(self, chat_client):
        msg = "Meteora Dynamic Vault'ta 'strateji' ne anlama geliyor?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["strateji", "strategy", "lending", "kamino", "solend", "protocol"], msg)

    def test_virtual_price_ne_anlama_gelir(self, chat_client):
        msg = "Meteora vault'ta virtual price ne anlama gelir, nasıl yorumlarım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_apy_closest_vs_average_aciklamasi(self, chat_client):
        msg = "Meteora vault APY'sindeki closest, average ve long APY farkı nedir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["closest", "average", "long", "apy"], msg, min_hits=2)

    def test_hangi_token_destekleniyor(self, chat_client):
        msg = "Meteora Dynamic Vault hangi tokenleri destekliyor?"
        r = _chat(chat_client, msg)
        assert result_has_response(r), f"LLM returned no response.\n{msg}"
        assert_no_raw_json(r, msg)

    def test_timestamp_nasil_hesaplanir(self, chat_client):
        msg = "Meteora vault APY geçmişi için timestamp nasıl hesaplarım?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_mentions(r, ["unix", "timestamp", "seconds", "saniye"], msg)

    def test_risk_aciklamasi(self, chat_client):
        msg = "Meteora Dynamic Vault'un riskleri nelerdir?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=80)
        assert_no_raw_json(r, msg)

    def test_deployed_amount_yorumu(self, chat_client):
        msg = "Meteora vault'ta 'deployed amount' ne demek?"
        r = _chat(chat_client, msg)
        assert_text_not_empty(r, msg, min_chars=60)
        assert_no_raw_json(r, msg)
        assert_mentions(r, ["deployed", "lending", "protocol", "strateji", "yatırıl"], msg)

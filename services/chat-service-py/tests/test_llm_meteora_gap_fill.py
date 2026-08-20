"""
Meteora — Gap Fill LLM Testleri

Mevcut test coverage'ında 15'in altında kalan endpoint'ler için ek senaryolar:
  - DLMM get_user_positions (+10 test → toplam 18)
  - DLMM get_active_bin    (+5  test → toplam 18)
  - DLMM get_pair          (+5  test → toplam 19)
  - DAMMv1 alpha_vault_cfgs(+5  test → toplam 19)
  - DAMMv1 pool_configs     (+5  test → toplam 19)

Her test sınıfı farklı bir kullanıcı senaryosunu kapsar:
  - LLM'in doğru endpoint'i tetikleyip tetiklemediği
  - Parametrelerin doğru çıkarılıp çıkarılmadığı
  - Yanıt kalitesi: sayısal veri, ham JSON yok, halüsinasyon yok
  - Türkçe + İngilizce karma sorgular
  - Edge case'ler: eksik parametre, birden fazla wallet, karmaşık doğal dil
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

DLMM_SOL_USDC  = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"
DLMM_SOL_USDT  = "ARwi1S4DaiTG5DX7S4M4ZkL2tiPGmYXLCxaXtpVXMiAm"
DLMM_BONK_SOL  = "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EkdEcsL5"
DLMM_JTO_USDC  = "7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm"
DLMM_WIF_SOL   = "CYbD9RaToYMtWKA7QZyoLahnHdWq553Vm62Lh6qWtuxq"
DLMM_JUP_USDC  = "3c3nBWFkKhKbhvNJLT5XDyPVWVnBaqb1AEb5FgYSTFyL"

DV1_JTO_USDC   = "6UmmUiYoBjSrhakAobJw8BYkjzKyVdwPko8mmspnaeFV"
DV1_MSOL_SOL   = "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP"
DV1_SOL_USDC   = "HcjZvfeSNJbNkfLD4eEcVBr987W65zqKvvcaYkM7SkAs"

SOL_MINT   = "So11111111111111111111111111111111111111112"
USDC_MINT  = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK_MINT  = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

WALLET_A   = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"
WALLET_B   = "7nYabs9dUhvxYwdTnrWVBL129NNrvYxvBBVsbkr2dwQW"
WALLET_C   = "3FZbgi29cpjq2GjdwV8eyHuJJnkLtktZc5SE5CBHGDyP"
WALLET_D   = "EwwGELa7GwFWJdGSmjBcWkBsqnDAfGFHe7qyiZVQCfBV"


# ─── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def chat_client():
    try:
        r = httpx.get(f"{CHAT_URL}/health", timeout=3.0)
        if r.status_code not in (200, 204):
            pytest.skip(f"chat-service did not respond: HTTP {r.status_code}")
    except Exception as exc:
        pytest.skip(f"chat-service ulaşılamıyor: {exc}")
    with httpx.Client(base_url=CHAT_URL, timeout=120.0) as c:
        yield c


def _headers() -> dict:
    return {
        "X-Internal-Api-Key": INTERNAL_KEY,
        "X-User-Wallet": TEST_WALLET,
        "Content-Type": "application/json",
    }


def _chat(client: httpx.Client, message: str):
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
        return [x.get("type", "") for x in self.actions + self.queries]

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
        f"'{t}' tetiklenmedi. Tetiklenenler: {r.types()}\nLLM: {r.text[:400]}\n{msg}"
    )


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
    bad = ["gerçek zamanlı veriye erişimim yok", "i don't have access to real-time",
           "as of my knowledge cutoff", "i cannot access live"]
    for b in bad:
        assert b not in r.tl(), f"Hallucination: '{b}'\nText: {r.text[:400]}\n{msg}"


def explains_error(r: StreamResult, msg: str = ""):
    kws = ["hata", "geçersiz", "bulunamadı", "error", "invalid", "not found", "sorry", "üzgün"]
    assert any(k in r.tl() for k in kws) or r.asked_clarification(), (
        f"Hata için açıklama yok.\n{r.text[:400]}\n{msg}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_user_positions (Gap fill: +10 senaryo)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMUserPositionsExtra:
    """DLMM get_user_positions: 10 ek senaryo — farklı cüzdanlar, PnL analizi, out-of-range"""

    def test_out_of_range_positions_query(self, chat_client):
        r = _chat(chat_client, "DLMM'deki kaç pozisyonum aralık dışına çıktı?")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        no_raw_json(r)
        no_hallucination(r)

    def test_positions_wallet_b(self, chat_client):
        r = _chat(chat_client, f"Show DLMM liquidity positions for wallet {WALLET_B}")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        has_param(r, "DLMM_GET_USER_POSITIONS", "walletAddress", contains=WALLET_B)

    def test_positions_wallet_c(self, chat_client):
        r = _chat(chat_client, f"Şu cüzdanın DLMM pozisyonlarını göster: {WALLET_C}")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        has_param(r, "DLMM_GET_USER_POSITIONS", "walletAddress", contains=WALLET_C)

    def test_positions_wallet_d_fee_summary(self, chat_client):
        r = _chat(chat_client, f"Wallet {WALLET_D} için DLMM kazançlarını ve ücret gelirini özetle")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        has_param(r, "DLMM_GET_USER_POSITIONS", "walletAddress", contains=WALLET_D)
        no_raw_json(r)

    def test_total_liquidity_provided(self, chat_client):
        r = _chat(chat_client, "How much total liquidity have I provided across all DLMM pools?")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        no_raw_json(r)
        has_number(r)
        no_hallucination(r)

    def test_bonk_sol_position_check(self, chat_client):
        r = _chat(chat_client, f"Do I have a position in the BONK-SOL DLMM pool {DLMM_BONK_SOL}?")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        no_raw_json(r)

    def test_wif_sol_positions(self, chat_client):
        r = _chat(chat_client, "WIF-SOL DLMM havuzunda pozisyonum var mı?")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        no_raw_json(r)
        no_hallucination(r)

    def test_rebalance_recommendation(self, chat_client):
        r = _chat(chat_client, (
            "My DLMM positions may need rebalancing. "
            "Can you check my current positions first?"
        ))
        triggered(r, "DLMM_GET_USER_POSITIONS")
        no_raw_json(r)
        assert r.has_text()

    def test_position_value_usd(self, chat_client):
        r = _chat(chat_client, "DLMM pozisyonlarımın toplam USD değeri nedir?")
        triggered(r, "DLMM_GET_USER_POSITIONS")
        no_raw_json(r)
        has_number(r)
        no_hallucination(r)

    def test_withdraw_before_check(self, chat_client):
        r = _chat(chat_client, (
            "I'm thinking of withdrawing liquidity from DLMM. "
            "Show me all my current positions first."
        ))
        triggered(r, "DLMM_GET_USER_POSITIONS")
        no_raw_json(r)
        assert r.has_text()


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_active_bin (Gap fill: +5 senaryo)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMActiveBinExtra:
    """DLMM get_active_bin: 5 ek senaryo — LP stratejisi, bin range, karşılaştırma"""

    def test_active_bin_before_add_liquidity(self, chat_client):
        r = _chat(chat_client, (
            f"Before I add liquidity to pool {DLMM_JUP_USDC}, "
            "what's the current active bin?"
        ))
        triggered(r, "DLMM_GET_ACTIVE_BIN")
        has_param(r, "DLMM_GET_ACTIVE_BIN", "poolAddress", contains=DLMM_JUP_USDC)
        no_raw_json(r)

    def test_active_bin_price_spread(self, chat_client):
        r = _chat(chat_client, f"DLMM havuzu {DLMM_WIF_SOL} için aktif bin'in fiyat aralığı ne?")
        triggered(r, "DLMM_GET_ACTIVE_BIN")
        no_raw_json(r)
        has_number(r)

    def test_active_bin_jto_usdc(self, chat_client):
        r = _chat(chat_client, f"JTO-USDC DLMM havuzunun aktif bin ID'sini öğren: {DLMM_JTO_USDC}")
        triggered(r, "DLMM_GET_ACTIVE_BIN")
        has_param(r, "DLMM_GET_ACTIVE_BIN", "poolAddress", contains=DLMM_JTO_USDC)

    def test_active_price_for_strategy(self, chat_client):
        r = _chat(chat_client, (
            f"I want to set a tight range strategy on {DLMM_SOL_USDC}. "
            "What is the current active price?"
        ))
        triggered(r, "DLMM_GET_ACTIVE_BIN")
        no_raw_json(r)
        has_number(r)
        no_hallucination(r)

    def test_active_bin_turkish_full_sentence(self, chat_client):
        r = _chat(chat_client, (
            f"Meteora DLMM havuzum {DLMM_BONK_SOL} için şu anki aktif işlem fiyatını "
            "ve bin numarasını öğrenmek istiyorum"
        ))
        triggered(r, "DLMM_GET_ACTIVE_BIN")
        has_param(r, "DLMM_GET_ACTIVE_BIN", "poolAddress", contains=DLMM_BONK_SOL)
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DLMM — get_pair (Gap fill: +5 senaryo)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLMMGetPairExtra:
    """DLMM get_pair: 5 ek senaryo — farklı tokenlara göre, LP analizi"""

    def test_pair_jup_usdc(self, chat_client):
        r = _chat(chat_client, f"Get details for JUP-USDC DLMM pool: {DLMM_JUP_USDC}")
        triggered(r, "DLMM_GET_PAIR")
        has_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_JUP_USDC)

    def test_pair_bin_step_interpretation(self, chat_client):
        r = _chat(chat_client, (
            f"Pool {DLMM_SOL_USDT} için bin step kaç ve bu ne anlama geliyor?"
        ))
        triggered(r, "DLMM_GET_PAIR")
        has_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_SOL_USDT)
        no_raw_json(r)
        mentions(r, ["bin", "step", "adım"], min_hits=1)

    def test_pair_total_liquidity_breakdown(self, chat_client):
        r = _chat(chat_client, (
            f"DLMM pool {DLMM_SOL_USDC} için token X ve token Y rezervlerini göster"
        ))
        triggered(r, "DLMM_GET_PAIR")
        no_raw_json(r)
        has_number(r)

    def test_pair_created_at_age(self, chat_client):
        r = _chat(chat_client, f"Bu DLMM havuzu ne zaman oluşturuldu? {DLMM_BONK_SOL}")
        triggered(r, "DLMM_GET_PAIR")
        has_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_BONK_SOL)
        no_raw_json(r)
        no_hallucination(r)

    def test_pair_permission_type(self, chat_client):
        r = _chat(chat_client, (
            f"Is DLMM pool {DLMM_JTO_USDC} permissioned or permissionless? "
            "What are the trading parameters?"
        ))
        triggered(r, "DLMM_GET_PAIR")
        has_param(r, "DLMM_GET_PAIR", "poolAddress", contains=DLMM_JTO_USDC)
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# DAMMv1 — alpha_vault_configs (Gap fill: +5 senaryo)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1AlphaVaultConfigsExtra:
    """DAMMv1 alpha_vault_configs: 5 ek senaryo — farklı kullanıcı niyetleri"""

    def test_configs_how_many_available(self, chat_client):
        r = _chat(chat_client, "Meteora'da kaç tane farklı alpha vault konfigürasyonu var?")
        triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        no_raw_json(r)
        has_number(r)
        no_hallucination(r)

    def test_configs_pro_rata_vs_fcfs(self, chat_client):
        r = _chat(chat_client, (
            "What alpha vault config types does Meteora support? "
            "I'm looking for pro-rata vs first-come-first-served options."
        ))
        triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        no_raw_json(r)
        assert r.has_text()

    def test_configs_deposit_cap(self, chat_client):
        r = _chat(chat_client, "Alpha vault konfigürasyonlarında deposit limiti nasıl ayarlanıyor?")
        triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        no_raw_json(r)

    def test_configs_escrow_vault(self, chat_client):
        r = _chat(chat_client, "Meteora alpha vault konfigürasyonlarını çek, escrow seçenekleri var mı?")
        triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        no_raw_json(r)
        no_hallucination(r)

    def test_configs_response_structured(self, chat_client):
        r = _chat(chat_client, (
            "List all Meteora alpha vault config options with their key parameters. "
            "I want to understand the differences."
        ))
        triggered(r, "DAMMV1_GET_ALPHA_VAULT_CONFIGS")
        no_raw_json(r)
        assert r.has_text()


# ═══════════════════════════════════════════════════════════════════════════════
# DAMMv1 — pool_configs (Gap fill: +5 senaryo)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDV1PoolConfigsExtra:
    """DAMMv1 pool_configs: 5 ek senaryo — yeni havuz kurulumu, fee tier seçimi"""

    def test_pool_configs_stable_curve(self, chat_client):
        r = _chat(chat_client, "DAMM v1'de stable curve seçeneği var mı? Pool configs listele.")
        triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        no_raw_json(r)
        no_hallucination(r)

    def test_pool_configs_dynamic_fee(self, chat_client):
        r = _chat(chat_client, "DAMM v1 dynamic fee konfigürasyonu içeren pool config'leri nelerdir?")
        triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        no_raw_json(r)
        mentions(r, ["fee", "dynamic", "dinamik", "ücret"], min_hits=1)

    def test_pool_configs_amplification_factor(self, chat_client):
        r = _chat(chat_client, (
            "What amplification factor options are available in DAMM v1 pool configs?"
        ))
        triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        no_raw_json(r)

    def test_pool_configs_for_lp_token(self, chat_client):
        r = _chat(chat_client, (
            "Yeni bir DAMM v1 havuzu açmak istiyorum. "
            "Hangi pool config template'leri mevcut?"
        ))
        triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        no_raw_json(r)
        assert r.has_text()
        no_hallucination(r)

    def test_pool_configs_fee_range(self, chat_client):
        r = _chat(chat_client, "What is the fee range available in DAMM v1 pool configurations?")
        triggered(r, "DAMMV1_GET_POOL_CONFIGS")
        no_raw_json(r)
        has_number(r)
        mentions(r, ["fee", "%", "bps", "basis"], min_hits=1)

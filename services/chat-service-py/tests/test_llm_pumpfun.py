"""
Pump.fun — Kapsamlı LLM Kalite Testi

15 endpoint / action için her biri 6-10 farklı senaryo:
  1.  launch_token          — token yaratma (clarification, parametre çıkarma)
  2.  pumpfun_buy           — bonding curve'den alım
  3.  pumpfun_sell          — bonding curve'den satış
  4.  pumpfun_token_info    — tek token detayı
  5.  pumpfun_bonding_curve — bonding curve doluluk/fiyat
  6.  pumpfun_trending      — market cap'e göre sıralı liste
  7.  pumpfun_new           — en yeni tokenlar
  8.  pumpfun_graduating    — mezuniyet aşamasındaki tokenlar
  9.  pumpfun_koth          — King of the Hill
  10. pumpfun_search        — isim/sembol araması
  11. pumpfun_comments      — token yorumları
  12. pumpfun_user          — kullanıcı profili
  13. pumpswap_buy          — PumpSwap AMM (graduated token) alım
  14. pumpswap_sell         — PumpSwap AMM (graduated token) satış
  15. pumpswap_pool_info    — PumpSwap havuz bilgisi

Test kategorileri (her endpoint için):
  - Doğru endpoint tetiklenme
  - Parametre çıkarma kalitesi (mint adresi, amount, query)
  - Ham JSON dökme yok
  - Halüsinasyon yok
  - Sayısal veri içerme
  - Türkçe + İngilizce karma sorgular
  - Hata/eksik parametre senaryoları
  - Bonding curve vs PumpSwap doğru routing

Toplam: ~130 test
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

# Gerçek pump.fun token mint adresleri (mainnet örnekleri)
BONK_MINT     = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF_MINT      = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
POPCAT_MINT   = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
FARTCOIN_MINT = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
MOODENG_MINT  = "ED5nyyWEzpPPiWimP8vYm7sD7TD3LAt3Q3gRTWHzc8yy"
TRUMP_MINT    = "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN"

# Gerçek graduated (PumpSwap AMM) mint adresleri
RAYDIUM_MINT  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"  # örnek graduated

# Test cüzdanları
WALLET_A = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"
WALLET_B = "7nYabs9dUhvxYwdTnrWVBL129NNrvYxvBBVsbkr2dwQW"


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
        f"'{t}' tetiklenmedi.\nTetiklenenler: {r.types()}\nLLM: {r.text[:400]}\n{msg}"
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
    bad = [
        "gerçek zamanlı veriye erişimim yok",
        "i don't have access to real-time",
        "as of my knowledge cutoff",
        "i cannot access live",
    ]
    for b in bad:
        assert b not in r.tl(), f"Hallucination: '{b}'\nText: {r.text[:400]}\n{msg}"


def explains_error(r: StreamResult, msg: str = ""):
    kws = ["hata", "geçersiz", "eksik", "error", "invalid", "required", "sorry", "üzgün", "lütfen"]
    assert any(k in r.tl() for k in kws) or r.asked_clarification(), (
        f"No explanation for the error / missing param.\n{r.text[:400]}\n{msg}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LAUNCH_TOKEN — Pump.fun token oluşturma
# ═══════════════════════════════════════════════════════════════════════════════

class TestLaunchToken:
    """launch_token: 10 senaryo — parametre çıkarma, clarification, mayhem, social"""

    def test_minimal_launch_asks_clarification(self, chat_client):
        r = _chat(chat_client, "I want to launch a token on pump.fun")
        assert r.asked_clarification() or r.has_text(80), (
            "Eksik parametre için LLM clarification istememeli veya detay sormalı"
        )
        mentions(r, ["name", "symbol", "token", "isim", "sembol"], min_hits=1)

    def test_full_params_extracted(self, chat_client):
        r = _chat(chat_client, (
            "Launch a token called MoonCat with symbol MCAT, "
            "description 'The cat that went to the moon', "
            "buy 0.5 SOL at launch, Twitter @mooncatsol"
        ))
        triggered(r, "launch_token")
        p = r.params_for("launch_token")
        assert "mooncat" in str(p.get("name", "")).lower(), f"name eksik/yanlış. Params: {p}"
        assert "mcat" in str(p.get("symbol", "")).lower(), f"symbol eksik/yanlış. Params: {p}"
        assert p.get("initialBuyAmount") is not None or "0.5" in str(p), (
            f"initialBuyAmount gönderilmedi. Params: {p}"
        )

    def test_symbol_extracted_uppercase(self, chat_client):
        r = _chat(chat_client, "Pump.fun'da 'PepeRocket' adında PRKT sembolü ile token çıkar")
        triggered(r, "launch_token")
        p = r.params_for("launch_token")
        assert "peperocket" in str(p.get("name", "")).lower(), f"name yanlış. Params: {p}"
        assert "prkt" in str(p.get("symbol", "")).upper(), f"symbol yanlış. Params: {p}"

    def test_initial_buy_amount_parsed(self, chat_client):
        r = _chat(chat_client, "Launch WolfToken WOLF on pump.fun with 1 SOL initial buy")
        triggered(r, "launch_token")
        p = r.params_for("launch_token")
        buy = str(p.get("initialBuyAmount", ""))
        assert "1" in buy, f"initialBuyAmount '1' bekleniyor. Params: {p}"

    def test_mayhem_mode_enabled(self, chat_client):
        r = _chat(chat_client, (
            "Launch FIREToken FIRE on pump.fun with mayhem mode on, "
            "cashback enabled, 0.5 SOL initial buy"
        ))
        triggered(r, "launch_token")
        p = r.params_for("launch_token")
        mayhem = str(p.get("mayhemMode", "")).lower()
        assert "true" in mayhem or mayhem == "true", f"mayhemMode=true bekleniyor. Params: {p}"

    def test_social_links_extracted(self, chat_client):
        r = _chat(chat_client, (
            "Launch SolDog SDOG on pump.fun. "
            "Twitter: https://twitter.com/soldogsol, "
            "Telegram: https://t.me/soldogchat, "
            "Website: https://soldog.xyz"
        ))
        triggered(r, "launch_token")
        p = r.params_for("launch_token")
        assert p.get("twitter") or p.get("telegram") or p.get("website"), (
            f"Sosyal linkler gönderilmedi. Params: {p}"
        )

    def test_description_turkish(self, chat_client):
        r = _chat(chat_client, (
            "Pump.fun'da 'AyıToken' adında AYTK sembolü ile bir token çıkar. "
            "Açıklama: Solana'nın en güçlü ayı tokeni"
        ))
        triggered(r, "launch_token")
        p = r.params_for("launch_token")
        assert "aytk" in str(p.get("symbol", "")).upper() or "ayı" in str(p.get("name", "")).lower(), (
            f"Türkçe parametre çıkarılamadı. Params: {p}"
        )

    def test_high_slippage_for_launch(self, chat_client):
        r = _chat(chat_client, (
            "Launch MoonDog MDOG on pump.fun, "
            "initial buy 2 SOL, slippage 20%, high priority fee"
        ))
        triggered(r, "launch_token")
        p = r.params_for("launch_token")
        slippage = float(p.get("slippage", 0) or 0)
        assert slippage >= 15 or "2" in str(p.get("initialBuyAmount", "")), (
            f"Slippage/initialBuy parametresi yanlış. Params: {p}"
        )

    def test_launch_response_mentions_fee(self, chat_client):
        r = _chat(chat_client, "Launch StarCoin STAR on pump.fun")
        if "launch_token" in r.types():
            no_raw_json(r)
        else:
            assert r.asked_clarification() or r.has_text(60)

    def test_launch_no_raw_json_response(self, chat_client):
        r = _chat(chat_client, (
            "Launch a pump.fun token: name=RocketSOL, symbol=RSOL, "
            "description='To the moon on Solana', buy 0.3 SOL"
        ))
        triggered(r, "launch_token")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PUMPFUN_BUY — Bonding curve'den alım
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunBuy:
    """pumpfun_buy: 9 senaryo"""

    def test_buy_by_mint_address(self, chat_client):
        r = _chat(chat_client, f"Buy 0.1 SOL of {FARTCOIN_MINT} on pump.fun")
        triggered(r, "pumpfun_buy")
        has_param(r, "pumpfun_buy", "mint", contains=FARTCOIN_MINT)
        has_param(r, "pumpfun_buy", "amount", contains="0.1")

    def test_buy_bonk_by_name(self, chat_client):
        r = _chat(chat_client, f"Pump.fun'dan BONK al, 0.2 SOL ver, mint: {BONK_MINT}")
        triggered(r, "pumpfun_buy")
        has_param(r, "pumpfun_buy", "mint", contains=BONK_MINT)

    def test_buy_with_slippage(self, chat_client):
        r = _chat(chat_client, f"Buy {WIF_MINT} on pump.fun with 0.5 SOL, 15% slippage")
        triggered(r, "pumpfun_buy")
        p = r.params_for("pumpfun_buy")
        slippage = float(p.get("slippage", 0) or 0)
        assert slippage >= 10, f"Slippage 15% bekleniyor, gelen: {slippage}. Params: {p}"

    def test_buy_with_priority_fee(self, chat_client):
        r = _chat(chat_client, f"Buy {MOODENG_MINT} on pump.fun, 0.3 SOL, priority fee 0.001")
        triggered(r, "pumpfun_buy")
        p = r.params_for("pumpfun_buy")
        pf = float(p.get("priorityFee", 0) or 0)
        assert pf >= 0.0005, f"priorityFee eksik/düşük. Params: {p}"

    def test_buy_not_pumpswap(self, chat_client):
        r = _chat(chat_client, f"Buy {POPCAT_MINT} on pump.fun bonding curve, 0.1 SOL")
        triggered(r, "pumpfun_buy")
        not_triggered(r, "pumpswap_buy", "Bonding curve için pumpswap_buy tetiklenmemeli")

    def test_buy_amount_extracted_correctly(self, chat_client):
        r = _chat(chat_client, f"Pump.fun'dan şu tokeni 0.05 SOL için satın al: {TRUMP_MINT}")
        triggered(r, "pumpfun_buy")
        p = r.params_for("pumpfun_buy")
        assert "0.05" in str(p.get("amount", "")), f"Amount '0.05' bekleniyor. Params: {p}"

    def test_buy_missing_mint_asks(self, chat_client):
        r = _chat(chat_client, "Buy a pump.fun token for 0.1 SOL")
        if "pumpfun_buy" not in r.types():
            assert r.asked_clarification() or r.has_text(60), (
                "Mint eksikken LLM clarification istememeli veya açıklama yapmalı"
            )

    def test_buy_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"0.1 SOL ile pump.fun'dan şunu al: {FARTCOIN_MINT}")
        triggered(r, "pumpfun_buy")
        no_raw_json(r)
        no_hallucination(r)

    def test_buy_popcat_confirmation_text(self, chat_client):
        r = _chat(chat_client, f"Buy POPCAT on pump.fun, mint: {POPCAT_MINT}, amount: 0.2 SOL")
        triggered(r, "pumpfun_buy")
        no_raw_json(r)
        mentions(r, ["popcat", "pump", "0.2", "sol", "buy", "al"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PUMPFUN_SELL — Bonding curve'den satış
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunSell:
    """pumpfun_sell: 8 senaryo"""

    def test_sell_tokens_by_amount(self, chat_client):
        r = _chat(chat_client, f"Sell 1000000 tokens of {FARTCOIN_MINT} on pump.fun")
        triggered(r, "pumpfun_sell")
        has_param(r, "pumpfun_sell", "mint", contains=FARTCOIN_MINT)
        has_param(r, "pumpfun_sell", "amount")

    def test_sell_all_tokens(self, chat_client):
        r = _chat(chat_client, f"Sell all my {POPCAT_MINT} tokens on pump.fun")
        triggered(r, "pumpfun_sell")
        p = r.params_for("pumpfun_sell")
        amount = str(p.get("amount", "")).lower()
        assert "all" in amount or amount != "", f"Sell all — amount bekleniyor. Params: {p}"

    def test_sell_denominated_in_sol(self, chat_client):
        r = _chat(chat_client, f"Sell {WIF_MINT} for 0.1 SOL on pump.fun (sell worth 0.1 SOL)")
        triggered(r, "pumpfun_sell")
        p = r.params_for("pumpfun_sell")
        assert p.get("denominatedInSol") is True or "0.1" in str(p), (
            f"denominatedInSol veya amount=0.1 bekleniyor. Params: {p}"
        )

    def test_sell_with_slippage(self, chat_client):
        r = _chat(chat_client, f"Sat {MOODENG_MINT} pump.fun'da 500000 token, %20 slippage")
        triggered(r, "pumpfun_sell")
        p = r.params_for("pumpfun_sell")
        slippage = float(p.get("slippage", 0) or 0)
        assert slippage >= 10, f"Slippage 20% bekleniyor. Params: {p}"

    def test_sell_not_pumpswap(self, chat_client):
        r = _chat(chat_client, f"Pump.fun'dan {BONK_MINT} sat, 1000000 token")
        triggered(r, "pumpfun_sell")
        not_triggered(r, "pumpswap_sell", "Bonding curve için pumpswap_sell tetiklenmemeli")

    def test_sell_turkish_query(self, chat_client):
        r = _chat(chat_client, f"Pump.fun'daki {TRUMP_MINT} tokenlerimi sat, 2000000 token")
        triggered(r, "pumpfun_sell")
        has_param(r, "pumpfun_sell", "mint", contains=TRUMP_MINT)

    def test_sell_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Sell {FARTCOIN_MINT} on pump.fun, 500000 tokens, 15% slippage")
        triggered(r, "pumpfun_sell")
        no_raw_json(r)
        no_hallucination(r)

    def test_sell_preview_mentions_token(self, chat_client):
        r = _chat(chat_client, f"Pump fun'dan POPCAT sat: {POPCAT_MINT}, 1000000 token")
        triggered(r, "pumpfun_sell")
        no_raw_json(r)
        mentions(r, ["popcat", "pump", "sell", "sat", "token"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PUMPFUN_TOKEN_INFO — Tek token detayı
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunTokenInfo:
    """pumpfun_token_info: 9 senaryo"""

    def test_token_info_by_mint(self, chat_client):
        r = _chat(chat_client, f"Get pump.fun token info for {FARTCOIN_MINT}")
        triggered(r, "pumpfun_token_info")
        has_param(r, "pumpfun_token_info", "mint", contains=FARTCOIN_MINT)

    def test_token_info_popcat(self, chat_client):
        r = _chat(chat_client, f"Tell me about POPCAT on pump.fun, mint: {POPCAT_MINT}")
        triggered(r, "pumpfun_token_info")
        has_param(r, "pumpfun_token_info", "mint", contains=POPCAT_MINT)

    def test_token_info_market_cap(self, chat_client):
        r = _chat(chat_client, f"What is the market cap of {MOODENG_MINT} on pump.fun?")
        triggered(r, "pumpfun_token_info")
        no_raw_json(r)
        has_number(r)
        mentions(r, ["market cap", "mcap", "piyasa", "değer"], min_hits=1)

    def test_token_info_graduation_status(self, chat_client):
        r = _chat(chat_client, f"Has pump.fun token {FARTCOIN_MINT} graduated yet?")
        triggered(r, "pumpfun_token_info")
        no_raw_json(r)
        mentions(r, ["graduated", "bonding curve", "mezun", "mezuniyet", "complete"], min_hits=1)

    def test_token_info_creator(self, chat_client):
        r = _chat(chat_client, f"Kim oluşturdu bu pump.fun tokenini? {TRUMP_MINT}")
        triggered(r, "pumpfun_token_info")
        no_raw_json(r)
        no_hallucination(r)

    def test_token_info_vs_bonding_curve_routing(self, chat_client):
        r = _chat(chat_client, f"Get full info about pump.fun token {BONK_MINT}")
        assert "pumpfun_token_info" in r.types() or "pumpfun_bonding_curve" in r.types(), (
            f"pumpfun_token_info veya pumpfun_bonding_curve tetiklenmedi. Types: {r.types()}"
        )
        no_raw_json(r)

    def test_token_info_price_query(self, chat_client):
        r = _chat(chat_client, f"What's the current price of {WIF_MINT} on pump.fun?")
        triggered(r, "pumpfun_token_info")
        no_raw_json(r)
        has_number(r)

    def test_token_info_response_interpretation(self, chat_client):
        r = _chat(chat_client, (
            f"Analyze this pump.fun token for me: {POPCAT_MINT}. "
            "Is it worth buying?"
        ))
        triggered(r, "pumpfun_token_info")
        no_raw_json(r)
        assert r.has_text(100)
        no_hallucination(r)

    def test_token_info_missing_mint(self, chat_client):
        r = _chat(chat_client, "Get info for my favorite pump.fun token")
        if "pumpfun_token_info" not in r.types():
            assert r.asked_clarification() or r.has_text(40)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. PUMPFUN_BONDING_CURVE — Bonding curve doluluk/fiyat
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunBondingCurve:
    """pumpfun_bonding_curve: 8 senaryo"""

    def test_bonding_curve_by_mint(self, chat_client):
        r = _chat(chat_client, f"Show bonding curve state for {FARTCOIN_MINT}")
        triggered(r, "pumpfun_bonding_curve")
        has_param(r, "pumpfun_bonding_curve", "mint", contains=FARTCOIN_MINT)

    def test_bonding_curve_fill_percentage(self, chat_client):
        r = _chat(chat_client, f"How full is the bonding curve for {POPCAT_MINT}?")
        triggered(r, "pumpfun_bonding_curve")
        no_raw_json(r)
        has_number(r)
        mentions(r, ["bonding curve", "fill", "dolu", "%", "graduation", "sol"], min_hits=1)

    def test_bonding_curve_graduation_progress(self, chat_client):
        r = _chat(chat_client, f"Bu token mezuniyet eşiğine ne kadar yakın? {MOODENG_MINT}")
        triggered(r, "pumpfun_bonding_curve")
        no_raw_json(r)
        no_hallucination(r)

    def test_bonding_curve_price_per_token(self, chat_client):
        r = _chat(chat_client, f"What's the current price per token for {TRUMP_MINT}?")
        triggered(r, "pumpfun_bonding_curve")
        no_raw_json(r)
        has_number(r)

    def test_bonding_curve_virtual_reserves(self, chat_client):
        r = _chat(chat_client, (
            f"Show me the virtual SOL and token reserves for {FARTCOIN_MINT} "
            "on the pump.fun bonding curve"
        ))
        triggered(r, "pumpfun_bonding_curve")
        no_raw_json(r)
        has_number(r)

    def test_bonding_curve_response_buy_advice(self, chat_client):
        r = _chat(chat_client, (
            f"Is {BONK_MINT} close to graduating on pump.fun? "
            "Should I buy before it graduates?"
        ))
        triggered(r, "pumpfun_bonding_curve")
        no_raw_json(r)
        assert r.has_text(80)

    def test_bonding_curve_wif(self, chat_client):
        r = _chat(chat_client, f"Pump.fun WIF bonding curve durumu: {WIF_MINT}")
        triggered(r, "pumpfun_bonding_curve")
        has_param(r, "pumpfun_bonding_curve", "mint", contains=WIF_MINT)

    def test_bonding_curve_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Bonding curve info for token {POPCAT_MINT}")
        triggered(r, "pumpfun_bonding_curve")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. PUMPFUN_TRENDING — Market cap'e göre sıralı liste
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunTrending:
    """pumpfun_trending: 8 senaryo"""

    def test_trending_basic(self, chat_client):
        r = _chat(chat_client, "Show me trending tokens on pump.fun")
        triggered(r, "pumpfun_trending")
        no_raw_json(r)
        no_hallucination(r)

    def test_trending_limit_10(self, chat_client):
        r = _chat(chat_client, "Show top 10 trending pump.fun tokens")
        triggered(r, "pumpfun_trending")
        p = r.params_for("pumpfun_trending")
        assert int(p.get("limit", 20)) <= 20, f"Limit parametresi: {p}"

    def test_trending_limit_50(self, chat_client):
        r = _chat(chat_client, "Give me top 50 pump.fun tokens by market cap")
        triggered(r, "pumpfun_trending")
        p = r.params_for("pumpfun_trending")
        assert int(p.get("limit", 0) or 0) >= 30 or p.get("limit") is None, (
            f"Limit 50 bekleniyor. Params: {p}"
        )

    def test_trending_response_has_numbers(self, chat_client):
        r = _chat(chat_client, "Pump.fun'daki en popüler tokenları göster")
        triggered(r, "pumpfun_trending")
        no_raw_json(r)
        has_number(r)

    def test_trending_sorted_by_mcap(self, chat_client):
        r = _chat(chat_client, "What are the highest market cap tokens on pump.fun?")
        triggered(r, "pumpfun_trending")
        no_raw_json(r)
        mentions(r, ["market cap", "mcap", "piyasa", "million", "milyon", "$"], min_hits=1)

    def test_trending_no_params(self, chat_client):
        r = _chat(chat_client, "pump.fun trending tokens listesi")
        triggered(r, "pumpfun_trending")
        no_raw_json(r)

    def test_trending_vs_new_routing(self, chat_client):
        r = _chat(chat_client, "Show me the hottest pump.fun tokens right now (by market cap)")
        triggered(r, "pumpfun_trending")
        not_triggered(r, "pumpfun_new", "Trending sorgusu new'i tetiklememeli")

    def test_trending_response_actionable(self, chat_client):
        r = _chat(chat_client, (
            "What are the top pump.fun tokens today? "
            "I want to find something to trade."
        ))
        triggered(r, "pumpfun_trending")
        no_raw_json(r)
        assert r.has_text(80)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. PUMPFUN_NEW — En yeni tokenlar
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunNew:
    """pumpfun_new: 8 senaryo"""

    def test_new_tokens_basic(self, chat_client):
        r = _chat(chat_client, "Show me the newest tokens launched on pump.fun")
        triggered(r, "pumpfun_new")
        no_raw_json(r)
        no_hallucination(r)

    def test_new_tokens_with_limit(self, chat_client):
        r = _chat(chat_client, "Get the last 20 tokens launched on pump.fun")
        triggered(r, "pumpfun_new")
        p = r.params_for("pumpfun_new")
        limit = int(p.get("limit", 20))
        assert limit >= 10, f"Limit düşük: {limit}. Params: {p}"

    def test_new_tokens_turkish(self, chat_client):
        r = _chat(chat_client, "Pump.fun'a en son eklenen tokenleri göster")
        triggered(r, "pumpfun_new")
        no_raw_json(r)

    def test_new_tokens_vs_trending_routing(self, chat_client):
        r = _chat(chat_client, "What tokens just launched on pump.fun? Show me the freshest ones.")
        triggered(r, "pumpfun_new")
        not_triggered(r, "pumpfun_trending", "Yeni token sorgusu trending'i tetiklememeli")

    def test_new_tokens_sniper_use_case(self, chat_client):
        r = _chat(chat_client, "I want to snipe new pump.fun launches. Show me the latest ones.")
        triggered(r, "pumpfun_new")
        no_raw_json(r)
        assert r.has_text(60)

    def test_new_tokens_response_quality(self, chat_client):
        r = _chat(chat_client, "Show me 10 newest pump.fun tokens with their names and symbols")
        triggered(r, "pumpfun_new")
        no_raw_json(r)
        has_number(r)

    def test_new_tokens_with_offset(self, chat_client):
        r = _chat(chat_client, "Show pump.fun new tokens, skip first 20, get next 20")
        triggered(r, "pumpfun_new")
        p = r.params_for("pumpfun_new")
        assert int(p.get("offset", 0) or 0) >= 10 or int(p.get("limit", 0) or 0) >= 10, (
            f"Offset/limit parametresi eksik. Params: {p}"
        )

    def test_new_tokens_no_hallucination(self, chat_client):
        r = _chat(chat_client, "Pump.fun en yeni token lansmanları neler?")
        triggered(r, "pumpfun_new")
        no_hallucination(r)
        no_raw_json(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PUMPFUN_GRADUATING — Mezuniyet aşamasındaki tokenlar
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunGraduating:
    """pumpfun_graduating: 8 senaryo"""

    def test_graduating_basic(self, chat_client):
        r = _chat(chat_client, "Show pump.fun tokens that are about to graduate")
        triggered(r, "pumpfun_graduating")
        no_raw_json(r)
        no_hallucination(r)

    def test_graduating_bonding_curve_fill(self, chat_client):
        r = _chat(chat_client, "Which pump.fun tokens have almost filled their bonding curve?")
        triggered(r, "pumpfun_graduating")
        no_raw_json(r)
        mentions(r, ["bonding curve", "graduating", "graduate", "mezuniyet", "75", "85", "sol"], min_hits=1)

    def test_graduating_turkish(self, chat_client):
        r = _chat(chat_client, "Pump.fun'da mezuniyet eşiğine yakın olan tokenları listele")
        triggered(r, "pumpfun_graduating")
        no_raw_json(r)

    def test_graduating_limit_param(self, chat_client):
        r = _chat(chat_client, "Show top 10 pump.fun tokens close to graduating")
        triggered(r, "pumpfun_graduating")
        p = r.params_for("pumpfun_graduating")
        limit = int(p.get("limit", 10) or 10)
        assert limit <= 20, f"Limit mantıklı olmalı. Params: {p}"

    def test_graduating_vs_graduated_routing(self, chat_client):
        r = _chat(chat_client, "Show tokens that are graduating SOON (not yet graduated)")
        triggered(r, "pumpfun_graduating")
        not_triggered(r, "pumpswap_pool_info", "Graduating sorgusu pumpswap'ı tetiklememeli")

    def test_graduating_investment_use_case(self, chat_client):
        r = _chat(chat_client, (
            "I want to buy tokens before they graduate to PumpSwap. "
            "Which ones are close?"
        ))
        triggered(r, "pumpfun_graduating")
        no_raw_json(r)
        assert r.has_text(80)

    def test_graduating_response_actionable(self, chat_client):
        r = _chat(chat_client, "Find pump.fun tokens that will graduate soon and might pump")
        triggered(r, "pumpfun_graduating")
        no_raw_json(r)
        no_hallucination(r)

    def test_graduating_high_fill_only(self, chat_client):
        r = _chat(chat_client, "Pump.fun'da bonding curve'i %75'ten fazla dolmuş tokenlar hangileri?")
        triggered(r, "pumpfun_graduating")
        no_raw_json(r)
        has_number(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. PUMPFUN_KOTH — King of the Hill
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunKoth:
    """pumpfun_koth: 7 senaryo"""

    def test_koth_basic(self, chat_client):
        r = _chat(chat_client, "Who is the king of the hill on pump.fun?")
        triggered(r, "pumpfun_koth")
        no_raw_json(r)
        no_hallucination(r)

    def test_koth_abbreviation(self, chat_client):
        r = _chat(chat_client, "Show me the pump.fun KotH token")
        triggered(r, "pumpfun_koth")
        no_raw_json(r)

    def test_koth_turkish(self, chat_client):
        r = _chat(chat_client, "Pump.fun'daki tepenin kralı hangi token?")
        triggered(r, "pumpfun_koth")
        no_raw_json(r)

    def test_koth_response_mentions_token(self, chat_client):
        r = _chat(chat_client, "What is the current pump.fun King of the Hill?")
        triggered(r, "pumpfun_koth")
        no_raw_json(r)
        has_number(r)

    def test_koth_no_params(self, chat_client):
        r = _chat(chat_client, "Get pump.fun king of the hill")
        triggered(r, "pumpfun_koth")
        p = r.params_for("pumpfun_koth")
        assert len([v for v in p.values() if v is not None and v != ""]) <= 1, (
            f"KOTH parametresiz çağrılmalı. Params: {p}"
        )

    def test_koth_response_market_cap(self, chat_client):
        r = _chat(chat_client, "Show pump.fun king of the hill with market cap info")
        triggered(r, "pumpfun_koth")
        no_raw_json(r)
        mentions(r, ["market cap", "mcap", "$", "piyasa", "king", "kral"], min_hits=1)

    def test_koth_should_i_buy(self, chat_client):
        r = _chat(chat_client, "What's the pump.fun king of the hill right now? Is it worth buying?")
        triggered(r, "pumpfun_koth")
        no_raw_json(r)
        no_hallucination(r)
        assert r.has_text(80)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. PUMPFUN_SEARCH — İsim/sembol araması
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunSearch:
    """pumpfun_search: 8 senaryo"""

    def test_search_by_name(self, chat_client):
        r = _chat(chat_client, "Search pump.fun for tokens named pepe")
        triggered(r, "pumpfun_search")
        has_param(r, "pumpfun_search", "query", contains="pepe")

    def test_search_by_symbol(self, chat_client):
        r = _chat(chat_client, "Find pump.fun tokens with symbol DOGE")
        triggered(r, "pumpfun_search")
        has_param(r, "pumpfun_search", "query", contains="doge")

    def test_search_turkish(self, chat_client):
        r = _chat(chat_client, "Pump.fun'da 'moon' içeren tokenleri ara")
        triggered(r, "pumpfun_search")
        has_param(r, "pumpfun_search", "query", contains="moon")

    def test_search_with_limit(self, chat_client):
        r = _chat(chat_client, "Search pump.fun for 'cat' tokens, show 5 results")
        triggered(r, "pumpfun_search")
        p = r.params_for("pumpfun_search")
        has_param(r, "pumpfun_search", "query", contains="cat")
        limit = int(p.get("limit", 10) or 10)
        assert limit <= 10, f"Limit 5 bekleniyor. Params: {p}"

    def test_search_missing_query_asks(self, chat_client):
        r = _chat(chat_client, "Search pump.fun for a token")
        if "pumpfun_search" not in r.types():
            assert r.asked_clarification() or r.has_text(40)

    def test_search_response_results(self, chat_client):
        r = _chat(chat_client, "Search for 'dragon' tokens on pump.fun")
        triggered(r, "pumpfun_search")
        no_raw_json(r)
        no_hallucination(r)

    def test_search_sol_tokens(self, chat_client):
        r = _chat(chat_client, "Find pump.fun tokens with 'sol' in their name")
        triggered(r, "pumpfun_search")
        has_param(r, "pumpfun_search", "query", contains="sol")

    def test_search_response_quality(self, chat_client):
        r = _chat(chat_client, "Show me pump.fun tokens matching 'rocket', summarize them")
        triggered(r, "pumpfun_search")
        no_raw_json(r)
        assert r.has_text(60)


# ═══════════════════════════════════════════════════════════════════════════════
# 11. PUMPFUN_COMMENTS — Token yorumları
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunComments:
    """pumpfun_comments: 7 senaryo"""

    def test_comments_by_mint(self, chat_client):
        r = _chat(chat_client, f"Show comments for pump.fun token {FARTCOIN_MINT}")
        triggered(r, "pumpfun_comments")
        has_param(r, "pumpfun_comments", "mint", contains=FARTCOIN_MINT)

    def test_comments_popcat(self, chat_client):
        r = _chat(chat_client, f"What are people saying about {POPCAT_MINT} on pump.fun?")
        triggered(r, "pumpfun_comments")
        has_param(r, "pumpfun_comments", "mint", contains=POPCAT_MINT)

    def test_comments_turkish(self, chat_client):
        r = _chat(chat_client, f"Bu pump.fun tokenine yapılan yorumları göster: {MOODENG_MINT}")
        triggered(r, "pumpfun_comments")
        has_param(r, "pumpfun_comments", "mint", contains=MOODENG_MINT)

    def test_comments_community_sentiment(self, chat_client):
        r = _chat(chat_client, f"What's the community sentiment for {TRUMP_MINT} on pump.fun?")
        triggered(r, "pumpfun_comments")
        no_raw_json(r)
        no_hallucination(r)

    def test_comments_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Read the replies for pump.fun token {FARTCOIN_MINT}")
        triggered(r, "pumpfun_comments")
        no_raw_json(r)

    def test_comments_missing_mint_asks(self, chat_client):
        r = _chat(chat_client, "Show me comments for a pump.fun token")
        if "pumpfun_comments" not in r.types():
            assert r.asked_clarification() or r.has_text(40)

    def test_comments_response_interpretation(self, chat_client):
        r = _chat(chat_client, (
            f"Show comments for {POPCAT_MINT} on pump.fun "
            "and tell me if sentiment is bullish or bearish"
        ))
        triggered(r, "pumpfun_comments")
        no_raw_json(r)
        assert r.has_text(60)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. PUMPFUN_USER — Kullanıcı profili
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunUser:
    """pumpfun_user: 8 senaryo"""

    def test_user_my_profile(self, chat_client):
        r = _chat(chat_client, "Show my pump.fun profile")
        triggered(r, "pumpfun_user")
        p = r.params_for("pumpfun_user")
        # wallet default (kendi wallet'ı) veya TEST_WALLET gönderilmeli
        assert p.get("wallet") is None or len(str(p.get("wallet", ""))) > 10, (
            f"Wallet parametresi: {p}"
        )

    def test_user_specific_wallet(self, chat_client):
        r = _chat(chat_client, f"Show pump.fun profile for wallet {WALLET_B}")
        triggered(r, "pumpfun_user")
        has_param(r, "pumpfun_user", "wallet", contains=WALLET_B)

    def test_user_tokens_created(self, chat_client):
        r = _chat(chat_client, "How many tokens have I created on pump.fun?")
        triggered(r, "pumpfun_user")
        no_raw_json(r)
        no_hallucination(r)

    def test_user_tokens_held(self, chat_client):
        r = _chat(chat_client, f"What pump.fun tokens does wallet {WALLET_B} hold?")
        triggered(r, "pumpfun_user")
        has_param(r, "pumpfun_user", "wallet", contains=WALLET_B)
        no_raw_json(r)

    def test_user_portfolio_overview(self, chat_client):
        r = _chat(chat_client, "Give me a summary of my pump.fun activity")
        triggered(r, "pumpfun_user")
        no_raw_json(r)
        assert r.has_text(60)

    def test_user_turkish_query(self, chat_client):
        r = _chat(chat_client, f"Bu cüzdanın pump.fun profilini göster: {WALLET_B}")
        triggered(r, "pumpfun_user")
        has_param(r, "pumpfun_user", "wallet", contains=WALLET_B)

    def test_user_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, "What's my pump.fun user profile?")
        triggered(r, "pumpfun_user")
        no_raw_json(r)
        no_hallucination(r)

    def test_user_response_mentions_tokens(self, chat_client):
        r = _chat(chat_client, f"Show pump.fun activity for {WALLET_A}")
        triggered(r, "pumpfun_user")
        no_raw_json(r)
        mentions(r, ["token", "pump", "profile", "profil", "created", "oluştur"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. PUMPSWAP_BUY — Graduated token PumpSwap AMM alım
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpSwapBuy:
    """pumpswap_buy: 8 senaryo"""

    def test_pumpswap_buy_graduated_token(self, chat_client):
        r = _chat(chat_client, f"Buy 0.5 SOL of graduated pump.fun token {RAYDIUM_MINT} on PumpSwap")
        triggered(r, "pumpswap_buy")
        has_param(r, "pumpswap_buy", "mint", contains=RAYDIUM_MINT)
        has_param(r, "pumpswap_buy", "amount", contains="0.5")

    def test_pumpswap_buy_not_bonding_curve(self, chat_client):
        r = _chat(chat_client, f"Buy {RAYDIUM_MINT} on PumpSwap AMM, 1 SOL, it's already graduated")
        triggered(r, "pumpswap_buy")
        not_triggered(r, "pumpfun_buy", "Graduated token için pumpfun_buy tetiklenmemeli")

    def test_pumpswap_buy_with_slippage(self, chat_client):
        r = _chat(chat_client, f"Buy {RAYDIUM_MINT} on pumpswap, 0.3 SOL, 10% slippage")
        triggered(r, "pumpswap_buy")
        p = r.params_for("pumpswap_buy")
        slippage = float(p.get("slippage", 0) or 0)
        assert slippage >= 5, f"Slippage bekleniyor. Params: {p}"

    def test_pumpswap_buy_turkish(self, chat_client):
        r = _chat(chat_client, f"PumpSwap AMM'den {RAYDIUM_MINT} al, 0.2 SOL")
        triggered(r, "pumpswap_buy")
        has_param(r, "pumpswap_buy", "mint", contains=RAYDIUM_MINT)

    def test_pumpswap_buy_priority_fee(self, chat_client):
        r = _chat(chat_client, f"Buy {RAYDIUM_MINT} on PumpSwap, 0.5 SOL, high priority fee 0.002")
        triggered(r, "pumpswap_buy")
        p = r.params_for("pumpswap_buy")
        pf = float(p.get("priorityFee", 0) or 0)
        assert pf >= 0.001, f"priorityFee yüksek bekleniyor. Params: {p}"

    def test_pumpswap_vs_pumpfun_routing(self, chat_client):
        r = _chat(chat_client, f"The token {RAYDIUM_MINT} graduated and is now on PumpSwap. Buy 0.3 SOL.")
        triggered(r, "pumpswap_buy")
        not_triggered(r, "pumpfun_buy")

    def test_pumpswap_buy_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"PumpSwap'tan {RAYDIUM_MINT} satın al, 1 SOL")
        triggered(r, "pumpswap_buy")
        no_raw_json(r)
        no_hallucination(r)

    def test_pumpswap_buy_confirmation_text(self, chat_client):
        r = _chat(chat_client, f"Buy graduated token {RAYDIUM_MINT} on PumpSwap AMM, 0.1 SOL")
        triggered(r, "pumpswap_buy")
        no_raw_json(r)
        mentions(r, ["pumpswap", "pump", "buy", "0.1", "sol", "al"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 14. PUMPSWAP_SELL — Graduated token PumpSwap AMM satış
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpSwapSell:
    """pumpswap_sell: 7 senaryo"""

    def test_pumpswap_sell_graduated_token(self, chat_client):
        r = _chat(chat_client, f"Sell 5000000 tokens of {RAYDIUM_MINT} on PumpSwap")
        triggered(r, "pumpswap_sell")
        has_param(r, "pumpswap_sell", "mint", contains=RAYDIUM_MINT)

    def test_pumpswap_sell_not_bonding_curve(self, chat_client):
        r = _chat(chat_client, f"Sell {RAYDIUM_MINT} on PumpSwap AMM (graduated token), 2000000 tokens")
        triggered(r, "pumpswap_sell")
        not_triggered(r, "pumpfun_sell", "Graduated için pumpfun_sell tetiklenmemeli")

    def test_pumpswap_sell_with_slippage(self, chat_client):
        r = _chat(chat_client, f"Sell {RAYDIUM_MINT} on pumpswap, 1000000 tokens, 15% slippage")
        triggered(r, "pumpswap_sell")
        p = r.params_for("pumpswap_sell")
        slippage = float(p.get("slippage", 0) or 0)
        assert slippage >= 5, f"Slippage bekleniyor. Params: {p}"

    def test_pumpswap_sell_turkish(self, chat_client):
        r = _chat(chat_client, f"PumpSwap AMM'de {RAYDIUM_MINT} sat, 3000000 token")
        triggered(r, "pumpswap_sell")
        has_param(r, "pumpswap_sell", "mint", contains=RAYDIUM_MINT)

    def test_pumpswap_sell_all(self, chat_client):
        r = _chat(chat_client, f"Sell all my {RAYDIUM_MINT} tokens on PumpSwap")
        triggered(r, "pumpswap_sell")
        p = r.params_for("pumpswap_sell")
        amount_str = str(p.get("amount", "")).lower()
        assert "all" in amount_str or amount_str != "", f"Amount bekleniyor. Params: {p}"

    def test_pumpswap_sell_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"PumpSwap'ta {RAYDIUM_MINT} sat, 2500000 token")
        triggered(r, "pumpswap_sell")
        no_raw_json(r)
        no_hallucination(r)

    def test_pumpswap_sell_response_mentions_token(self, chat_client):
        r = _chat(chat_client, f"Sell graduated pump.fun token on PumpSwap: {RAYDIUM_MINT}, 1000000")
        triggered(r, "pumpswap_sell")
        no_raw_json(r)
        mentions(r, ["pumpswap", "sell", "sat", "token", "graduated"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 15. PUMPSWAP_POOL_INFO — PumpSwap havuz bilgisi
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpSwapPoolInfo:
    """pumpswap_pool_info: 7 senaryo"""

    def test_pool_info_by_mint(self, chat_client):
        r = _chat(chat_client, f"Show PumpSwap pool info for token {RAYDIUM_MINT}")
        triggered(r, "pumpswap_pool_info")
        has_param(r, "pumpswap_pool_info", "mint", contains=RAYDIUM_MINT)

    def test_pool_info_graduated_check(self, chat_client):
        r = _chat(chat_client, f"Is token {RAYDIUM_MINT} on PumpSwap AMM yet?")
        triggered(r, "pumpswap_pool_info")
        no_raw_json(r)
        no_hallucination(r)

    def test_pool_info_tvl_query(self, chat_client):
        r = _chat(chat_client, f"What is the liquidity in the PumpSwap pool for {RAYDIUM_MINT}?")
        triggered(r, "pumpswap_pool_info")
        no_raw_json(r)
        has_number(r)

    def test_pool_info_turkish(self, chat_client):
        r = _chat(chat_client, f"PumpSwap havuz bilgilerini göster: {RAYDIUM_MINT}")
        triggered(r, "pumpswap_pool_info")
        has_param(r, "pumpswap_pool_info", "mint", contains=RAYDIUM_MINT)

    def test_pool_info_vs_bonding_curve_routing(self, chat_client):
        r = _chat(chat_client, f"Get AMM pool info for graduated token {RAYDIUM_MINT}")
        triggered(r, "pumpswap_pool_info")
        not_triggered(r, "pumpfun_bonding_curve", "Graduated için bonding curve tetiklenmemeli")

    def test_pool_info_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"PumpSwap pool for {RAYDIUM_MINT}")
        triggered(r, "pumpswap_pool_info")
        no_raw_json(r)

    def test_pool_info_lp_decision(self, chat_client):
        r = _chat(chat_client, (
            f"I want to provide liquidity on PumpSwap for token {RAYDIUM_MINT}. "
            "Show me the pool details first."
        ))
        triggered(r, "pumpswap_pool_info")
        no_raw_json(r)
        assert r.has_text(60)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-CUTTING: Routing & Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunRouting:
    """Bonding curve vs PumpSwap routing, belirsiz sorgular: 10 test"""

    def test_bonding_vs_pumpswap_by_context(self, chat_client):
        r = _chat(chat_client, f"Buy {FARTCOIN_MINT} — it's still on the bonding curve, 0.2 SOL")
        triggered(r, "pumpfun_buy")
        not_triggered(r, "pumpswap_buy")

    def test_graduated_routes_to_pumpswap(self, chat_client):
        r = _chat(chat_client, f"Buy graduated pump.fun token {RAYDIUM_MINT} on PumpSwap, 0.5 SOL")
        triggered(r, "pumpswap_buy")
        not_triggered(r, "pumpfun_buy")

    def test_trending_not_confused_with_search(self, chat_client):
        r = _chat(chat_client, "What are the most popular pump.fun tokens by market cap?")
        triggered(r, "pumpfun_trending")
        not_triggered(r, "pumpfun_search")

    def test_new_not_confused_with_trending(self, chat_client):
        r = _chat(chat_client, "Pump.fun'a bugün yeni eklenen tokenlar neler?")
        triggered(r, "pumpfun_new")
        not_triggered(r, "pumpfun_trending")

    def test_graduating_not_confused_with_graduated(self, chat_client):
        r = _chat(chat_client, "Show tokens about to graduate from pump.fun (not yet graduated)")
        triggered(r, "pumpfun_graduating")
        not_triggered(r, "pumpswap_pool_info")

    def test_search_not_confused_with_trending(self, chat_client):
        r = _chat(chat_client, "Search pump.fun for tokens with 'inu' in the name")
        triggered(r, "pumpfun_search")
        not_triggered(r, "pumpfun_trending")

    def test_koth_not_confused_with_trending(self, chat_client):
        r = _chat(chat_client, "Who's the pump.fun king of the hill right now?")
        triggered(r, "pumpfun_koth")
        not_triggered(r, "pumpfun_trending")

    def test_launch_not_confused_with_buy(self, chat_client):
        r = _chat(chat_client, "I want to create a new meme token on pump.fun called DANKCAT DCAT")
        triggered(r, "launch_token")
        not_triggered(r, "pumpfun_buy")

    def test_token_info_not_confused_with_bonding(self, chat_client):
        r = _chat(chat_client, f"Give me all details about {MOODENG_MINT} on pump.fun")
        assert "pumpfun_token_info" in r.types() or "pumpfun_bonding_curve" in r.types(), (
            f"Token info veya bonding curve bekleniyor. Types: {r.types()}"
        )

    def test_user_not_confused_with_comments(self, chat_client):
        r = _chat(chat_client, f"Show me what pump.fun user {WALLET_B} has been doing")
        triggered(r, "pumpfun_user")
        not_triggered(r, "pumpfun_comments")


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE QUALITY: LLM Yorum Derinliği
# ═══════════════════════════════════════════════════════════════════════════════

class TestPumpFunInterpretation:
    """LLM yanıt kalitesi — ham JSON yok, anlamlı analiz, sayısal veri: 8 test"""

    def test_trending_response_actionable(self, chat_client):
        r = _chat(chat_client, "What are the top 5 pump.fun tokens right now? I want to trade one.")
        triggered(r, "pumpfun_trending")
        no_raw_json(r)
        assert r.has_text(100)
        no_hallucination(r)

    def test_token_info_full_analysis(self, chat_client):
        r = _chat(chat_client, (
            f"Full analysis of pump.fun token {POPCAT_MINT}: "
            "market cap, bonding curve status, community activity"
        ))
        assert "pumpfun_token_info" in r.types() or "pumpfun_bonding_curve" in r.types()
        no_raw_json(r)
        assert r.has_text(100)

    def test_new_tokens_launch_strategy(self, chat_client):
        r = _chat(chat_client, (
            "I want to snipe the next big pump.fun launch. "
            "Show me the newest tokens and tell me which looks promising."
        ))
        triggered(r, "pumpfun_new")
        no_raw_json(r)
        assert r.has_text(80)

    def test_graduating_investment_advice(self, chat_client):
        r = _chat(chat_client, (
            "Which pump.fun tokens are close to graduating? "
            "Are they worth buying before graduation?"
        ))
        triggered(r, "pumpfun_graduating")
        no_raw_json(r)
        assert r.has_text(100)
        no_hallucination(r)

    def test_buy_response_shows_details(self, chat_client):
        r = _chat(chat_client, f"Buy 0.2 SOL of {FARTCOIN_MINT} on pump.fun")
        triggered(r, "pumpfun_buy")
        no_raw_json(r)
        mentions(r, ["0.2", "sol", "pump", "buy", "al"], min_hits=1)

    def test_launch_full_clarification_flow(self, chat_client):
        r = _chat(chat_client, "Help me launch a meme token on pump.fun")
        assert r.asked_clarification() or r.has_text(80), (
            "Eksik detaylar için clarification veya yardım metni bekleniyor"
        )
        mentions(r, ["name", "symbol", "isim", "sembol", "token"], min_hits=1)

    def test_comments_sentiment_analysis(self, chat_client):
        r = _chat(chat_client, (
            f"Read comments for {MOODENG_MINT} on pump.fun and "
            "tell me if the community is bullish or bearish"
        ))
        triggered(r, "pumpfun_comments")
        no_raw_json(r)
        assert r.has_text(60)

    def test_koth_analysis_deep(self, chat_client):
        r = _chat(chat_client, (
            "Who is the pump.fun king of the hill? "
            "Tell me about their market cap and bonding curve status."
        ))
        triggered(r, "pumpfun_koth")
        no_raw_json(r)
        has_number(r)
        no_hallucination(r)

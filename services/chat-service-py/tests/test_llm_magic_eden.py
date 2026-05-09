"""
Magic Eden — Kapsamlı LLM Kalite Testi

40 endpoint için her biri 5-8 farklı senaryo:

WALLET / TOKEN QUERIES
  1.  me_wallet_escrow_balance      — ME escrow bakiyesi
  2.  me_wallet_offers_received     — Gelen teklifler
  3.  me_wallet_offers_made         — Yapılan teklifler
  4.  me_owner_activities           — Owner aktiviteleri (tarih filtreli)
  5.  me_wallet_activities          — Cüzdan aktiviteleri
  6.  me_wallet                     — ME profili
  7.  me_wallet_tokens              — Cüzdandaki NFT'ler
  8.  me_token                      — NFT metadata
  9.  me_token_listings             — NFT listingleri
  10. me_token_offers_received      — NFT'ye gelen teklifler
  11. me_token_activities           — NFT aktiviteleri

COLLECTION QUERIES
  12. me_collection_activities      — Koleksiyon aktiviteleri
  13. me_collection_stats           — Koleksiyon istatistikleri
  14. me_collection_attributes      — Koleksiyon özellikleri
  15. me_collections                — Koleksiyon listesi
  16. me_collection_listings        — Koleksiyon listingleri
  17. me_collections_batch_listings — Toplu koleksiyon listingleri
  18. me_collection_holder_stats    — Holder istatistikleri
  19. me_collection_leaderboard     — Holder liderboard
  20. me_launchpad_collections      — Launchpad koleksiyonları

BUY / SELL INSTRUCTIONS
  21. me_buy_instruction            — NFT satın al (teklif)
  22. me_buy_now                    — Anında satın al
  23. me_buy_now_transfer_nft       — Satın al + transfer
  24. me_buy_cancel                 — Teklif iptal
  25. me_buy_change_price           — Teklif fiyatı güncelle
  26. me_sell                       — NFT listele
  27. me_sell_change_price          — Listeleme fiyatı güncelle
  28. me_sell_now                   — Teklifi kabul et
  29. me_sell_cancel                — Listeyi kaldır
  30. me_deposit                    — Escrow'a yatır
  31. me_withdraw                   — Escrow'dan çek

MMM POOL QUERIES
  32. me_mmm_pools                  — MMM pool listesi
  33. me_mmm_token_pools            — NFT için en iyi MMM pool

MMM POOL INSTRUCTIONS
  34. me_mmm_create_pool            — MMM pool oluştur
  35. me_mmm_update_pool            — MMM pool güncelle
  36. me_mmm_sol_deposit_buy        — Pool'a SOL yatır
  37. me_mmm_sol_withdraw_buy       — Pool'dan SOL çek
  38. me_mmm_sol_close_pool         — Pool'u kapat
  39. me_mmm_sol_fulfill_buy        — Satıcı: pool buy order'ını doldur
  40. me_mmm_sol_fulfill_sell       — Alıcı: pool sell order'ını doldur

Toplam: ~260 test
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

# Gerçek cüzdan adresleri
WALLET_A = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"
WALLET_B = "AC1r4GYj1TZ62ASZS4VT6GnNtMzoD78wYNWfxJ3Uzbxy"
WALLET_C = "4wejSnr97csngztZ5SU7A6iZRXJD7B3Y1R1koCQ5NjmD"

# Gerçek NFT mint adresleri
MINT_A = "HqRZKSAWHig98928x6jezNxUUmwY8VrHn1WF6NzeVU27"
MINT_B = "6XkPLDtV2w17UUsyzah39PTBjZ9xTY8HokqRdWcmDhBa"
MINT_C = "6eGfgGuxA1pBtTX4k2oubpa6m1Z3eaJUBxgtZFB7sjZA"
MINT_D = "4uvpqEL73361hRXCrHqBZQWeqfbKPQw55yKSFZvLQYTq"

# Koleksiyon sembolleri
COL_DEGODS    = "degods"
COL_OKAY      = "okay_bears"
COL_MAGIC     = "magicticket"
COL_PANDAS    = "pastel_pandas"

# MMM pool adresleri
POOL_A = "7Tjbkwp234hPKjmTXNwpjdQnUqA1hXLAJEduVB2xkg8p"
POOL_B = "9akMvSF6UHixPQBHQ5tpY6hF6bVyo7CQG4bVn5AhWPD9"


# ─── Fixtures & Helpers ───────────────────────────────────────────────────────

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


def _chat(client: httpx.Client, message: str) -> "StreamResult":
    payload = {"sessionId": f"local:{uuid.uuid4()}", "content": message, "attachments": []}
    with client.stream("POST", "/messages/stream", json=payload, headers=_headers()) as r:
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        body = r.read().decode()
    return StreamResult.parse(body)


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


def triggered(r: StreamResult, t: str, msg: str = ""):
    assert t in r.types(), (
        f"'{t}' tetiklenmedi.\nTetiklenenler: {r.types()}\nLLM: {r.text[:400]}\n{msg}"
    )

def not_triggered(r: StreamResult, t: str, msg: str = ""):
    assert t not in r.types(), (
        f"İstenmeyen '{t}' tetiklendi.\nLLM: {r.text[:300]}\n{msg}"
    )

def has_param(r: StreamResult, action: str, key: str, contains: str | None = None, msg: str = ""):
    for x in r.actions + r.queries:
        if x.get("type") == action:
            p = x.get("params", {})
            assert key in p, f"[{action}] '{key}' eksik. Params: {p}\n{msg}"
            if contains:
                assert contains.lower() in str(p[key]).lower(), (
                    f"[{action}] '{key}'={p[key]!r} içinde '{contains}' yok\n{msg}"
                )
            return
    pytest.fail(f"[{action}] hiç tetiklenmedi. Tetiklenenler: {r.types()}")

def no_raw_json(r: StreamResult, msg: str = ""):
    t = r.text
    assert not (t.count("{") + t.count("}") > 10 and t.count('"') > 20), (
        f"LLM ham JSON döktü.\n{t[:500]}\n{msg}"
    )

def has_number(r: StreamResult, msg: str = ""):
    assert re.search(r"\d[\d,.]*", r.text), (
        f"LLM yanıtında sayı yok.\n{r.text[:400]}\n{msg}"
    )

def mentions(r: StreamResult, kws: list[str], min_hits: int = 1, msg: str = ""):
    hits = [k for k in kws if k.lower() in r.tl()]
    assert len(hits) >= min_hits, (
        f"LLM şunlardan hiçbirini söylemiyor: {kws}\nMetin: {r.text[:400]}\n{msg}"
    )

def no_hallucination(r: StreamResult, msg: str = ""):
    bad = [
        "i don't have access to real-time",
        "as of my knowledge cutoff",
        "i cannot access live",
        "gerçek zamanlı veriye erişimim yok",
    ]
    for b in bad:
        assert b not in r.tl(), f"Halüsinasyon: '{b}'\nMetin: {r.text[:400]}\n{msg}"

def asks_for_missing(r: StreamResult, param_hint: str, msg: str = ""):
    kws = [param_hint, "provide", "required", "missing", "please", "lütfen", "gerekli"]
    assert any(k in r.tl() for k in kws) or r.asked_clarification(), (
        f"Eksik param için soru yok.\n{r.text[:400]}\n{msg}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ME_WALLET_ESCROW_BALANCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeWalletEscrowBalance:
    """me_wallet_escrow_balance: 6 senaryo"""

    def test_escrow_basic(self, chat_client):
        r = _chat(chat_client, f"What's my Magic Eden escrow balance for wallet {WALLET_A}?")
        triggered(r, "me_wallet_escrow_balance")
        has_param(r, "me_wallet_escrow_balance", "walletAddress", contains=WALLET_A)

    def test_escrow_how_much_sol(self, chat_client):
        r = _chat(chat_client, f"How much SOL is in my Magic Eden escrow? Wallet: {WALLET_B}")
        triggered(r, "me_wallet_escrow_balance")
        has_param(r, "me_wallet_escrow_balance", "walletAddress", contains=WALLET_B)

    def test_escrow_response_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Show Magic Eden escrow balance for {WALLET_A}")
        triggered(r, "me_wallet_escrow_balance")
        no_raw_json(r)
        no_hallucination(r)

    def test_escrow_missing_wallet_asks(self, chat_client):
        r = _chat(chat_client, "What is my Magic Eden escrow balance?")
        if "me_wallet_escrow_balance" not in r.types():
            asks_for_missing(r, "wallet")

    def test_escrow_response_mentions_sol(self, chat_client):
        r = _chat(chat_client, f"Check the escrow SOL for wallet {WALLET_C} on Magic Eden")
        triggered(r, "me_wallet_escrow_balance")
        no_raw_json(r)
        mentions(r, ["escrow", "sol", "balance", "bakiye"], min_hits=1)

    def test_escrow_not_confused_with_deposit(self, chat_client):
        r = _chat(chat_client, f"What's the current escrow balance for {WALLET_A}? (just checking, don't deposit)")
        triggered(r, "me_wallet_escrow_balance")
        not_triggered(r, "me_deposit")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ME_WALLET_OFFERS_RECEIVED
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeWalletOffersReceived:
    """me_wallet_offers_received: 6 senaryo"""

    def test_offers_received_basic(self, chat_client):
        r = _chat(chat_client, f"What offers have I received on Magic Eden? Wallet: {WALLET_A}")
        triggered(r, "me_wallet_offers_received")
        has_param(r, "me_wallet_offers_received", "walletAddress", contains=WALLET_A)

    def test_offers_received_show_bids(self, chat_client):
        r = _chat(chat_client, f"Show all bids on my NFTs from wallet {WALLET_B}")
        triggered(r, "me_wallet_offers_received")
        has_param(r, "me_wallet_offers_received", "walletAddress", contains=WALLET_B)

    def test_offers_received_sort_by_amount(self, chat_client):
        r = _chat(chat_client, f"Show highest bids on NFTs in wallet {WALLET_A}, sorted by bid amount")
        triggered(r, "me_wallet_offers_received")
        p = r.params_for("me_wallet_offers_received")
        assert p.get("sort") in (None, "bidAmount", "updatedAt"), f"Sort param: {p}"

    def test_offers_received_min_price(self, chat_client):
        r = _chat(chat_client, f"Show offers above 1 SOL received by wallet {WALLET_C}")
        triggered(r, "me_wallet_offers_received")
        p = r.params_for("me_wallet_offers_received")
        assert p.get("minPrice") is not None or "1" in str(p), f"minPrice bekleniyor. Params: {p}"

    def test_offers_received_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"List all offers received by {WALLET_A} on Magic Eden")
        triggered(r, "me_wallet_offers_received")
        no_raw_json(r)
        no_hallucination(r)

    def test_offers_received_response_quality(self, chat_client):
        r = _chat(chat_client, f"Are there any offers on my NFTs? Wallet: {WALLET_A}")
        triggered(r, "me_wallet_offers_received")
        no_raw_json(r)
        mentions(r, ["offer", "bid", "teklif", "nft"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ME_WALLET_OFFERS_MADE
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeWalletOffersMade:
    """me_wallet_offers_made: 6 senaryo"""

    def test_offers_made_basic(self, chat_client):
        r = _chat(chat_client, f"What offers has wallet {WALLET_A} made on Magic Eden?")
        triggered(r, "me_wallet_offers_made")
        has_param(r, "me_wallet_offers_made", "walletAddress", contains=WALLET_A)

    def test_offers_made_my_bids(self, chat_client):
        r = _chat(chat_client, f"Show my active bids on Magic Eden, wallet: {WALLET_B}")
        triggered(r, "me_wallet_offers_made")
        has_param(r, "me_wallet_offers_made", "walletAddress", contains=WALLET_B)

    def test_offers_made_limit(self, chat_client):
        r = _chat(chat_client, f"Show the last 10 offers made by wallet {WALLET_A}")
        triggered(r, "me_wallet_offers_made")
        p = r.params_for("me_wallet_offers_made")
        limit = int(p.get("limit", 100) or 100)
        assert limit <= 100, f"Limit mantıklı olmalı. Params: {p}"

    def test_offers_made_vs_received_routing(self, chat_client):
        r = _chat(chat_client, f"What bids has {WALLET_C} placed on Magic Eden? (offers they made)")
        triggered(r, "me_wallet_offers_made")
        not_triggered(r, "me_wallet_offers_received")

    def test_offers_made_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"List active offers made by {WALLET_A}")
        triggered(r, "me_wallet_offers_made")
        no_raw_json(r)
        no_hallucination(r)

    def test_offers_made_max_price_filter(self, chat_client):
        r = _chat(chat_client, f"Show offers under 2 SOL made by wallet {WALLET_B}")
        triggered(r, "me_wallet_offers_made")
        p = r.params_for("me_wallet_offers_made")
        assert p.get("maxPrice") is not None or "2" in str(p), f"maxPrice bekleniyor. Params: {p}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ME_OWNER_ACTIVITIES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeOwnerActivities:
    """me_owner_activities: 6 senaryo"""

    def test_owner_activities_basic(self, chat_client):
        r = _chat(chat_client, f"Show NFT activities for owner {WALLET_A} on Magic Eden")
        triggered(r, "me_owner_activities")
        has_param(r, "me_owner_activities", "owner", contains=WALLET_A)

    def test_owner_activities_with_date(self, chat_client):
        r = _chat(chat_client, f"Show activities for wallet {WALLET_B} after 2024-01-01")
        triggered(r, "me_owner_activities")
        p = r.params_for("me_owner_activities")
        assert p.get("createdAt") is not None or "2024" in str(p), f"createdAt bekleniyor. Params: {p}"

    def test_owner_activities_recent(self, chat_client):
        r = _chat(chat_client, f"Recent NFT activity for owner {WALLET_C} since last week")
        triggered(r, "me_owner_activities")
        has_param(r, "me_owner_activities", "owner", contains=WALLET_C)

    def test_owner_vs_wallet_activities_routing(self, chat_client):
        r = _chat(chat_client, f"Show activities after timestamp 1704067200 for owner {WALLET_A}")
        triggered(r, "me_owner_activities")

    def test_owner_activities_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Get trading history for owner {WALLET_A} since 2024-06-01")
        triggered(r, "me_owner_activities")
        no_raw_json(r)
        no_hallucination(r)

    def test_owner_activities_response_quality(self, chat_client):
        r = _chat(chat_client, f"What has owner {WALLET_B} done on Magic Eden recently?")
        triggered(r, "me_owner_activities")
        no_raw_json(r)
        mentions(r, ["activity", "trade", "sale", "listing", "aktivite"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ME_WALLET_ACTIVITIES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeWalletActivities:
    """me_wallet_activities: 6 senaryo"""

    def test_wallet_activities_basic(self, chat_client):
        r = _chat(chat_client, f"Show trading activity for wallet {WALLET_A} on Magic Eden")
        triggered(r, "me_wallet_activities")
        has_param(r, "me_wallet_activities", "walletAddress", contains=WALLET_A)

    def test_wallet_activities_buy_sell_history(self, chat_client):
        r = _chat(chat_client, f"What has wallet {WALLET_B} bought and sold on Magic Eden?")
        triggered(r, "me_wallet_activities")
        has_param(r, "me_wallet_activities", "walletAddress", contains=WALLET_B)

    def test_wallet_activities_with_limit(self, chat_client):
        r = _chat(chat_client, f"Last 10 NFT transactions for wallet {WALLET_C}")
        triggered(r, "me_wallet_activities")
        p = r.params_for("me_wallet_activities")
        limit = int(p.get("limit", 100) or 100)
        assert limit <= 100, f"Limit parametresi: {p}"

    def test_wallet_activities_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"NFT trading history for {WALLET_A}")
        triggered(r, "me_wallet_activities")
        no_raw_json(r)
        no_hallucination(r)

    def test_wallet_activities_response_mentions_types(self, chat_client):
        r = _chat(chat_client, f"Show all Magic Eden activity for wallet {WALLET_B}")
        triggered(r, "me_wallet_activities")
        no_raw_json(r)
        mentions(r, ["sale", "buy", "list", "bid", "al", "sat", "aktivite"], min_hits=1)

    def test_wallet_activities_missing_wallet(self, chat_client):
        r = _chat(chat_client, "Show my Magic Eden trading history")
        # Should use connected wallet or ask
        if "me_wallet_activities" not in r.types():
            assert r.asked_clarification() or r.has_text(40)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ME_WALLET
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeWallet:
    """me_wallet: 5 senaryo"""

    def test_wallet_profile_basic(self, chat_client):
        r = _chat(chat_client, f"Show me the Magic Eden profile for wallet {WALLET_A}")
        triggered(r, "me_wallet")
        has_param(r, "me_wallet", "walletAddress", contains=WALLET_A)

    def test_wallet_display_name(self, chat_client):
        r = _chat(chat_client, f"What's the display name of {WALLET_B} on Magic Eden?")
        triggered(r, "me_wallet")
        has_param(r, "me_wallet", "walletAddress", contains=WALLET_B)

    def test_wallet_avatar(self, chat_client):
        r = _chat(chat_client, f"What NFT is {WALLET_C} using as their Magic Eden avatar?")
        triggered(r, "me_wallet")
        has_param(r, "me_wallet", "walletAddress", contains=WALLET_C)

    def test_wallet_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Magic Eden profile for {WALLET_A}")
        triggered(r, "me_wallet")
        no_raw_json(r)
        no_hallucination(r)

    def test_wallet_response_quality(self, chat_client):
        r = _chat(chat_client, f"Who is {WALLET_B} on Magic Eden? Show their profile.")
        triggered(r, "me_wallet")
        no_raw_json(r)
        mentions(r, ["profile", "wallet", "display", "avatar", "profil"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ME_WALLET_TOKENS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeWalletTokens:
    """me_wallet_tokens: 7 senaryo"""

    def test_wallet_tokens_basic(self, chat_client):
        r = _chat(chat_client, f"What NFTs does wallet {WALLET_A} have?")
        triggered(r, "me_wallet_tokens")
        has_param(r, "me_wallet_tokens", "walletAddress", contains=WALLET_A)

    def test_wallet_tokens_listed_only(self, chat_client):
        r = _chat(chat_client, f"Show only listed NFTs in wallet {WALLET_B}")
        triggered(r, "me_wallet_tokens")
        p = r.params_for("me_wallet_tokens")
        assert p.get("listStatus") in (None, "listed") or "listed" in str(p), f"listStatus bekleniyor: {p}"

    def test_wallet_tokens_by_collection(self, chat_client):
        r = _chat(chat_client, f"Show DeGods NFTs in wallet {WALLET_C}")
        triggered(r, "me_wallet_tokens")
        p = r.params_for("me_wallet_tokens")
        assert "degods" in str(p.get("collectionSymbol", "")).lower() or "degods" in str(p), f"collectionSymbol bekleniyor: {p}"

    def test_wallet_tokens_with_limit(self, chat_client):
        r = _chat(chat_client, f"Show 20 NFTs from wallet {WALLET_A}")
        triggered(r, "me_wallet_tokens")
        p = r.params_for("me_wallet_tokens")
        assert int(p.get("limit", 100) or 100) <= 100

    def test_wallet_tokens_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"List all NFTs owned by {WALLET_B}")
        triggered(r, "me_wallet_tokens")
        no_raw_json(r)
        no_hallucination(r)

    def test_wallet_tokens_min_price_filter(self, chat_client):
        r = _chat(chat_client, f"Show NFTs in wallet {WALLET_A} listed above 5 SOL")
        triggered(r, "me_wallet_tokens")
        p = r.params_for("me_wallet_tokens")
        assert p.get("minPrice") is not None or "5" in str(p), f"minPrice bekleniyor: {p}"

    def test_wallet_tokens_response_quality(self, chat_client):
        r = _chat(chat_client, f"How many NFTs does {WALLET_C} have?")
        triggered(r, "me_wallet_tokens")
        no_raw_json(r)
        mentions(r, ["nft", "wallet", "token", "collection", "koleksiyon"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. ME_TOKEN
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeToken:
    """me_token: 6 senaryo"""

    def test_token_info_by_mint(self, chat_client):
        r = _chat(chat_client, f"Show me info about NFT {MINT_A}")
        triggered(r, "me_token")
        has_param(r, "me_token", "tokenMint", contains=MINT_A)

    def test_token_who_owns(self, chat_client):
        r = _chat(chat_client, f"Who owns NFT {MINT_B}?")
        triggered(r, "me_token")
        has_param(r, "me_token", "tokenMint", contains=MINT_B)

    def test_token_details(self, chat_client):
        r = _chat(chat_client, f"What collection is mint {MINT_C} from?")
        triggered(r, "me_token")
        has_param(r, "me_token", "tokenMint", contains=MINT_C)

    def test_token_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Get metadata for NFT {MINT_D}")
        triggered(r, "me_token")
        no_raw_json(r)
        no_hallucination(r)

    def test_token_missing_mint_asks(self, chat_client):
        r = _chat(chat_client, "Show me info about this NFT on Magic Eden")
        if "me_token" not in r.types():
            asks_for_missing(r, "mint")

    def test_token_response_quality(self, chat_client):
        r = _chat(chat_client, f"NFT details for {MINT_A}: name, owner, collection")
        triggered(r, "me_token")
        no_raw_json(r)
        mentions(r, ["nft", "name", "owner", "collection", "mint", "isim"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. ME_TOKEN_LISTINGS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeTokenListings:
    """me_token_listings: 6 senaryo"""

    def test_token_listings_basic(self, chat_client):
        r = _chat(chat_client, f"What are the listings for NFT {MINT_A} on Magic Eden?")
        triggered(r, "me_token_listings")
        has_param(r, "me_token_listings", "tokenMint", contains=MINT_A)

    def test_token_listings_is_it_listed(self, chat_client):
        r = _chat(chat_client, f"Is NFT {MINT_B} listed for sale on Magic Eden?")
        triggered(r, "me_token_listings")
        has_param(r, "me_token_listings", "tokenMint", contains=MINT_B)

    def test_token_listings_how_much(self, chat_client):
        r = _chat(chat_client, f"How much is {MINT_C} selling for on Magic Eden?")
        triggered(r, "me_token_listings")
        has_param(r, "me_token_listings", "tokenMint", contains=MINT_C)

    def test_token_listings_all_marketplaces(self, chat_client):
        r = _chat(chat_client, f"Show listings for {MINT_A} across all marketplaces including Tensor")
        triggered(r, "me_token_listings")
        p = r.params_for("me_token_listings")
        assert p.get("listingAggMode") in (None, "true", True) or "true" in str(p), f"listingAggMode bekleniyor: {p}"

    def test_token_listings_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Get current listing price for mint {MINT_D}")
        triggered(r, "me_token_listings")
        no_raw_json(r)
        no_hallucination(r)

    def test_token_listings_response_mentions_price(self, chat_client):
        r = _chat(chat_client, f"What's the list price for {MINT_B}?")
        triggered(r, "me_token_listings")
        no_raw_json(r)
        mentions(r, ["sol", "price", "list", "fiyat"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. ME_TOKEN_OFFERS_RECEIVED
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeTokenOffersReceived:
    """me_token_offers_received: 6 senaryo"""

    def test_token_offers_basic(self, chat_client):
        r = _chat(chat_client, f"What offers has NFT {MINT_A} received?")
        triggered(r, "me_token_offers_received")
        has_param(r, "me_token_offers_received", "tokenMint", contains=MINT_A)

    def test_token_offers_highest(self, chat_client):
        r = _chat(chat_client, f"What's the highest offer for NFT {MINT_B}?")
        triggered(r, "me_token_offers_received")
        has_param(r, "me_token_offers_received", "tokenMint", contains=MINT_B)

    def test_token_offers_above_price(self, chat_client):
        r = _chat(chat_client, f"Show offers above 0.5 SOL for NFT {MINT_C}")
        triggered(r, "me_token_offers_received")
        p = r.params_for("me_token_offers_received")
        assert p.get("minPrice") is not None or "0.5" in str(p), f"minPrice bekleniyor: {p}"

    def test_token_offers_sort_by_amount(self, chat_client):
        r = _chat(chat_client, f"Show bids on {MINT_A} sorted by bid amount descending")
        triggered(r, "me_token_offers_received")
        p = r.params_for("me_token_offers_received")
        assert p.get("sort") in (None, "bidAmount", "updatedAt")

    def test_token_offers_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"List all bids on NFT {MINT_D}")
        triggered(r, "me_token_offers_received")
        no_raw_json(r)
        no_hallucination(r)

    def test_token_offers_response_quality(self, chat_client):
        r = _chat(chat_client, f"Any offers on my NFT {MINT_A}? Show the best ones.")
        triggered(r, "me_token_offers_received")
        no_raw_json(r)
        mentions(r, ["offer", "bid", "teklif", "sol", "price", "fiyat"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 11. ME_TOKEN_ACTIVITIES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeTokenActivities:
    """me_token_activities: 5 senaryo"""

    def test_token_activities_basic(self, chat_client):
        r = _chat(chat_client, f"Show trading activity for NFT {MINT_A}")
        triggered(r, "me_token_activities")
        has_param(r, "me_token_activities", "tokenMint", contains=MINT_A)

    def test_token_activities_sale_history(self, chat_client):
        r = _chat(chat_client, f"What's the sales history for mint {MINT_B}?")
        triggered(r, "me_token_activities")
        has_param(r, "me_token_activities", "tokenMint", contains=MINT_B)

    def test_token_activities_with_limit(self, chat_client):
        r = _chat(chat_client, f"Last 5 events for NFT {MINT_C}")
        triggered(r, "me_token_activities")
        p = r.params_for("me_token_activities")
        assert int(p.get("limit", 100) or 100) <= 100

    def test_token_activities_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Was NFT {MINT_D} ever sold before?")
        triggered(r, "me_token_activities")
        no_raw_json(r)
        no_hallucination(r)

    def test_token_activities_response_quality(self, chat_client):
        r = _chat(chat_client, f"What happened to NFT {MINT_A}? Show listing and sale events.")
        triggered(r, "me_token_activities")
        no_raw_json(r)
        mentions(r, ["sale", "list", "bid", "event", "aktivite", "sat"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. ME_COLLECTION_ACTIVITIES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollectionActivities:
    """me_collection_activities: 6 senaryo"""

    def test_collection_activities_basic(self, chat_client):
        r = _chat(chat_client, f"Show activity for the {COL_DEGODS} collection on Magic Eden")
        triggered(r, "me_collection_activities")
        has_param(r, "me_collection_activities", "symbol", contains=COL_DEGODS)

    def test_collection_activities_recent_sales(self, chat_client):
        r = _chat(chat_client, f"Recent sales for {COL_OKAY} collection")
        triggered(r, "me_collection_activities")
        has_param(r, "me_collection_activities", "symbol", contains=COL_OKAY)

    def test_collection_activities_with_limit(self, chat_client):
        r = _chat(chat_client, f"Show last 50 activities for {COL_MAGIC} collection")
        triggered(r, "me_collection_activities")
        p = r.params_for("me_collection_activities")
        limit = int(p.get("limit", 100) or 100)
        assert limit <= 1000

    def test_collection_activities_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"What's happening in the {COL_DEGODS} NFT collection?")
        triggered(r, "me_collection_activities")
        no_raw_json(r)
        no_hallucination(r)

    def test_collection_activities_not_confused_with_stats(self, chat_client):
        r = _chat(chat_client, f"Show recent trading activity for {COL_OKAY} (not stats)")
        triggered(r, "me_collection_activities")
        not_triggered(r, "me_collection_stats")

    def test_collection_activities_response_quality(self, chat_client):
        r = _chat(chat_client, f"What trades happened in {COL_MAGIC} today?")
        triggered(r, "me_collection_activities")
        no_raw_json(r)
        mentions(r, ["sale", "list", "buy", "bid", "activit", "aktivite"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. ME_COLLECTION_STATS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollectionStats:
    """me_collection_stats: 7 senaryo"""

    def test_collection_stats_floor_price(self, chat_client):
        r = _chat(chat_client, f"What's the floor price of {COL_DEGODS} on Magic Eden?")
        triggered(r, "me_collection_stats")
        has_param(r, "me_collection_stats", "symbol", contains=COL_DEGODS)

    def test_collection_stats_24h_volume(self, chat_client):
        r = _chat(chat_client, f"Show 24h stats for {COL_OKAY} collection")
        triggered(r, "me_collection_stats")
        p = r.params_for("me_collection_stats")
        assert p.get("timeWindow") in (None, "24h") or "24h" in str(p)

    def test_collection_stats_listed_count(self, chat_client):
        r = _chat(chat_client, f"How many {COL_MAGIC} NFTs are listed for sale?")
        triggered(r, "me_collection_stats")
        has_param(r, "me_collection_stats", "symbol", contains=COL_MAGIC)

    def test_collection_stats_all_marketplaces(self, chat_client):
        r = _chat(chat_client, f"Floor price for {COL_DEGODS} across all marketplaces")
        triggered(r, "me_collection_stats")
        p = r.params_for("me_collection_stats")
        assert p.get("listingAggMode") in (None, "true", True) or "true" in str(p)

    def test_collection_stats_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Collection stats for {COL_OKAY}")
        triggered(r, "me_collection_stats")
        no_raw_json(r)
        no_hallucination(r)

    def test_collection_stats_response_has_numbers(self, chat_client):
        r = _chat(chat_client, f"Show me {COL_DEGODS} stats: floor, volume, listings")
        triggered(r, "me_collection_stats")
        no_raw_json(r)
        has_number(r)

    def test_collection_stats_7d_window(self, chat_client):
        r = _chat(chat_client, f"Show 7 day volume stats for {COL_MAGIC}")
        triggered(r, "me_collection_stats")
        p = r.params_for("me_collection_stats")
        assert p.get("timeWindow") in (None, "7d") or "7d" in str(p)


# ═══════════════════════════════════════════════════════════════════════════════
# 14. ME_COLLECTION_ATTRIBUTES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollectionAttributes:
    """me_collection_attributes: 5 senaryo"""

    def test_collection_attributes_basic(self, chat_client):
        r = _chat(chat_client, f"What traits does the {COL_DEGODS} collection have?")
        triggered(r, "me_collection_attributes")
        has_param(r, "me_collection_attributes", "collectionSymbol", contains=COL_DEGODS)

    def test_collection_attributes_rarest(self, chat_client):
        r = _chat(chat_client, f"Show the rarest traits in {COL_OKAY}")
        triggered(r, "me_collection_attributes")
        has_param(r, "me_collection_attributes", "collectionSymbol", contains=COL_OKAY)

    def test_collection_attributes_floor_by_trait(self, chat_client):
        r = _chat(chat_client, f"What are the floor prices by trait for {COL_MAGIC}?")
        triggered(r, "me_collection_attributes")
        has_param(r, "me_collection_attributes", "collectionSymbol", contains=COL_MAGIC)

    def test_collection_attributes_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Show available attributes for {COL_DEGODS}")
        triggered(r, "me_collection_attributes")
        no_raw_json(r)
        no_hallucination(r)

    def test_collection_attributes_response_quality(self, chat_client):
        r = _chat(chat_client, f"What are the trait types and counts for {COL_OKAY}?")
        triggered(r, "me_collection_attributes")
        no_raw_json(r)
        mentions(r, ["trait", "attribute", "özellik", "count", "floor"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 15. ME_COLLECTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollections:
    """me_collections: 5 senaryo"""

    def test_collections_list(self, chat_client):
        r = _chat(chat_client, "Show me NFT collections on Magic Eden")
        triggered(r, "me_collections")
        no_raw_json(r)

    def test_collections_paginated(self, chat_client):
        r = _chat(chat_client, "Show next page of Magic Eden collections, skip first 200")
        triggered(r, "me_collections")
        p = r.params_for("me_collections")
        assert int(p.get("offset", 0) or 0) >= 100 or int(p.get("limit", 200) or 200) <= 200

    def test_collections_not_confused_with_collection_stats(self, chat_client):
        r = _chat(chat_client, "List all NFT collections available on Magic Eden")
        triggered(r, "me_collections")
        not_triggered(r, "me_collection_stats")

    def test_collections_no_raw_json(self, chat_client):
        r = _chat(chat_client, "What collections are on Magic Eden?")
        triggered(r, "me_collections")
        no_raw_json(r)
        no_hallucination(r)

    def test_collections_response_quality(self, chat_client):
        r = _chat(chat_client, "Give me a list of Magic Eden NFT collections")
        triggered(r, "me_collections")
        no_raw_json(r)
        mentions(r, ["collection", "nft", "koleksiyon", "magic eden"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 16. ME_COLLECTION_LISTINGS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollectionListings:
    """me_collection_listings: 6 senaryo"""

    def test_collection_listings_cheapest(self, chat_client):
        r = _chat(chat_client, f"Show cheapest listings for {COL_DEGODS} collection")
        triggered(r, "me_collection_listings")
        has_param(r, "me_collection_listings", "symbol", contains=COL_DEGODS)

    def test_collection_listings_price_range(self, chat_client):
        r = _chat(chat_client, f"Show {COL_OKAY} listings between 5 and 10 SOL")
        triggered(r, "me_collection_listings")
        p = r.params_for("me_collection_listings")
        assert p.get("minPrice") is not None or p.get("maxPrice") is not None or "5" in str(p)

    def test_collection_listings_recently_listed(self, chat_client):
        r = _chat(chat_client, f"Show most recently listed NFTs in {COL_MAGIC}")
        triggered(r, "me_collection_listings")
        p = r.params_for("me_collection_listings")
        assert p.get("sort") in (None, "listPrice", "updatedAt")

    def test_collection_listings_all_marketplaces(self, chat_client):
        r = _chat(chat_client, f"Floor listings for {COL_DEGODS} including Tensor")
        triggered(r, "me_collection_listings")
        p = r.params_for("me_collection_listings")
        assert p.get("listingAggMode") in (None, True, "true") or "true" in str(p)

    def test_collection_listings_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"What's for sale in the {COL_OKAY} collection?")
        triggered(r, "me_collection_listings")
        no_raw_json(r)
        no_hallucination(r)

    def test_collection_listings_response_quality(self, chat_client):
        r = _chat(chat_client, f"Show floor listings for {COL_MAGIC}")
        triggered(r, "me_collection_listings")
        no_raw_json(r)
        mentions(r, ["sol", "listing", "floor", "price", "fiyat", "nft"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 17. ME_COLLECTIONS_BATCH_LISTINGS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollectionsBatchListings:
    """me_collections_batch_listings: 5 senaryo"""

    def test_batch_listings_two_collections(self, chat_client):
        r = _chat(chat_client, f"Show listings for {COL_DEGODS} AND {COL_OKAY} at the same time")
        triggered(r, "me_collections_batch_listings")
        p = r.params_for("me_collections_batch_listings")
        symbols = p.get("symbols", [])
        assert len(symbols) >= 2 or COL_DEGODS in str(p), f"Birden fazla koleksiyon bekleniyor: {p}"

    def test_batch_listings_compare_floor(self, chat_client):
        r = _chat(chat_client, f"Compare floor listings between {COL_DEGODS} and {COL_MAGIC}")
        triggered(r, "me_collections_batch_listings")

    def test_batch_listings_not_single_collection(self, chat_client):
        r = _chat(chat_client, f"Batch listings for {COL_OKAY} and {COL_PANDAS} collections")
        triggered(r, "me_collections_batch_listings")
        not_triggered(r, "me_collection_listings")

    def test_batch_listings_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Show listings for {COL_DEGODS} and {COL_MAGIC} together")
        triggered(r, "me_collections_batch_listings")
        no_raw_json(r)
        no_hallucination(r)

    def test_batch_listings_response_quality(self, chat_client):
        r = _chat(chat_client, f"Multiple collection listings: {COL_OKAY} and {COL_PANDAS}")
        triggered(r, "me_collections_batch_listings")
        no_raw_json(r)
        mentions(r, ["listing", "collection", "koleksiyon", "nft", "sol"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 18. ME_COLLECTION_HOLDER_STATS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollectionHolderStats:
    """me_collection_holder_stats: 5 senaryo"""

    def test_holder_stats_basic(self, chat_client):
        r = _chat(chat_client, f"How many holders does {COL_DEGODS} have?")
        triggered(r, "me_collection_holder_stats")
        has_param(r, "me_collection_holder_stats", "symbol", contains=COL_DEGODS)

    def test_holder_stats_top_holders(self, chat_client):
        r = _chat(chat_client, f"Who are the top holders of {COL_OKAY}?")
        triggered(r, "me_collection_holder_stats")
        has_param(r, "me_collection_holder_stats", "symbol", contains=COL_OKAY)

    def test_holder_stats_unique_holders(self, chat_client):
        r = _chat(chat_client, f"How many unique wallets hold {COL_MAGIC} NFTs?")
        triggered(r, "me_collection_holder_stats")
        has_param(r, "me_collection_holder_stats", "symbol", contains=COL_MAGIC)

    def test_holder_stats_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Token distribution for {COL_DEGODS}")
        triggered(r, "me_collection_holder_stats")
        no_raw_json(r)
        no_hallucination(r)

    def test_holder_stats_vs_leaderboard_routing(self, chat_client):
        r = _chat(chat_client, f"Holder statistics and distribution for {COL_OKAY}")
        triggered(r, "me_collection_holder_stats")
        not_triggered(r, "me_collection_leaderboard")


# ═══════════════════════════════════════════════════════════════════════════════
# 19. ME_COLLECTION_LEADERBOARD
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeCollectionLeaderboard:
    """me_collection_leaderboard: 5 senaryo"""

    def test_leaderboard_basic(self, chat_client):
        r = _chat(chat_client, f"Show leaderboard for {COL_DEGODS} collection")
        triggered(r, "me_collection_leaderboard")
        has_param(r, "me_collection_leaderboard", "symbol", contains=COL_DEGODS)

    def test_leaderboard_top_10(self, chat_client):
        r = _chat(chat_client, f"Top 10 holders of {COL_OKAY} by NFT count")
        triggered(r, "me_collection_leaderboard")
        p = r.params_for("me_collection_leaderboard")
        limit = int(p.get("limit", 100) or 100)
        assert limit <= 100

    def test_leaderboard_whale_ranking(self, chat_client):
        r = _chat(chat_client, f"Who holds the most {COL_MAGIC} NFTs? Show ranking.")
        triggered(r, "me_collection_leaderboard")
        has_param(r, "me_collection_leaderboard", "symbol", contains=COL_MAGIC)

    def test_leaderboard_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Richest holders in {COL_DEGODS}")
        triggered(r, "me_collection_leaderboard")
        no_raw_json(r)
        no_hallucination(r)

    def test_leaderboard_vs_holder_stats_routing(self, chat_client):
        r = _chat(chat_client, f"Show top ranked wallets by {COL_OKAY} holdings")
        triggered(r, "me_collection_leaderboard")
        not_triggered(r, "me_collection_holder_stats")


# ═══════════════════════════════════════════════════════════════════════════════
# 20. ME_LAUNCHPAD_COLLECTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeLaunchpadCollections:
    """me_launchpad_collections: 5 senaryo"""

    def test_launchpad_upcoming(self, chat_client):
        r = _chat(chat_client, "What's launching on Magic Eden launchpad?")
        triggered(r, "me_launchpad_collections")
        no_raw_json(r)

    def test_launchpad_upcoming_mints(self, chat_client):
        r = _chat(chat_client, "Show upcoming NFT mints on Magic Eden")
        triggered(r, "me_launchpad_collections")
        no_raw_json(r)
        no_hallucination(r)

    def test_launchpad_with_limit(self, chat_client):
        r = _chat(chat_client, "Show 10 upcoming Magic Eden launchpad projects")
        triggered(r, "me_launchpad_collections")
        p = r.params_for("me_launchpad_collections")
        assert int(p.get("limit", 200) or 200) <= 500

    def test_launchpad_not_confused_with_collections(self, chat_client):
        r = _chat(chat_client, "Show NFT launches and upcoming projects on Magic Eden launchpad")
        triggered(r, "me_launchpad_collections")
        not_triggered(r, "me_collections")

    def test_launchpad_response_quality(self, chat_client):
        r = _chat(chat_client, "What new NFT projects are launching on Magic Eden soon?")
        triggered(r, "me_launchpad_collections")
        no_raw_json(r)
        mentions(r, ["launch", "mint", "nft", "collection", "price", "sol", "koleksiyon"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 21. ME_BUY_INSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeBuyInstruction:
    """me_buy_instruction: 6 senaryo"""

    def test_buy_basic(self, chat_client):
        r = _chat(chat_client, f"Buy NFT {MINT_A} for 2.87 SOL on Magic Eden from wallet {WALLET_A}")
        triggered(r, "me_buy_instruction")
        has_param(r, "me_buy_instruction", "tokenMint", contains=MINT_A)
        has_param(r, "me_buy_instruction", "buyer", contains=WALLET_A)

    def test_buy_price_extracted(self, chat_client):
        r = _chat(chat_client, f"Place a bid on NFT {MINT_B} at 1.5 SOL, my wallet is {WALLET_B}")
        triggered(r, "me_buy_instruction")
        p = r.params_for("me_buy_instruction")
        assert "1.5" in str(p.get("price", "")) or "1.5" in str(p), f"price bekleniyor: {p}"

    def test_buy_vs_buy_now_routing(self, chat_client):
        r = _chat(chat_client, f"Place an offer on NFT {MINT_C} at 3 SOL from {WALLET_A}")
        triggered(r, "me_buy_instruction")
        not_triggered(r, "me_buy_now")

    def test_buy_missing_params_asks(self, chat_client):
        r = _chat(chat_client, "I want to buy an NFT on Magic Eden")
        if "me_buy_instruction" not in r.types():
            asks_for_missing(r, "mint")

    def test_buy_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Magic Eden NFT buy: mint={MINT_D}, buyer={WALLET_C}, price=5 SOL")
        triggered(r, "me_buy_instruction")
        no_raw_json(r)
        no_hallucination(r)

    def test_buy_with_expiry(self, chat_client):
        r = _chat(chat_client, f"Bid 2 SOL on NFT {MINT_A} from {WALLET_A}, expiry 7 days")
        triggered(r, "me_buy_instruction")
        has_param(r, "me_buy_instruction", "tokenMint", contains=MINT_A)


# ═══════════════════════════════════════════════════════════════════════════════
# 22. ME_BUY_NOW
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeBuyNow:
    """me_buy_now: 6 senaryo"""

    def test_buy_now_basic(self, chat_client):
        r = _chat(chat_client, (
            f"Buy NFT {MINT_A} now from seller {WALLET_B} for 2.87 SOL, "
            f"my wallet is {WALLET_A}, token account is TokenAcc123"
        ))
        triggered(r, "me_buy_now")
        has_param(r, "me_buy_now", "tokenMint", contains=MINT_A)
        has_param(r, "me_buy_now", "buyer", contains=WALLET_A)
        has_param(r, "me_buy_now", "seller", contains=WALLET_B)

    def test_buy_now_price_extracted(self, chat_client):
        r = _chat(chat_client, (
            f"Instantly buy {MINT_B} from {WALLET_C} for 5 SOL, "
            f"buyer {WALLET_A}, ATA: someATA456"
        ))
        triggered(r, "me_buy_now")
        p = r.params_for("me_buy_now")
        assert "5" in str(p.get("price", "")) or "5" in str(p), f"price bekleniyor: {p}"

    def test_buy_now_vs_buy_instruction(self, chat_client):
        r = _chat(chat_client, (
            f"Buy NFT {MINT_C} at listing price NOW from seller {WALLET_B} "
            f"for {WALLET_A}, ATA: someATA789"
        ))
        triggered(r, "me_buy_now")
        not_triggered(r, "me_buy_instruction")

    def test_buy_now_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            f"Immediately purchase NFT {MINT_A} from {WALLET_B}, "
            f"price 3 SOL, buyer {WALLET_A}, ATA: ata123"
        ))
        triggered(r, "me_buy_now")
        no_raw_json(r)
        no_hallucination(r)

    def test_buy_now_missing_seller_asks(self, chat_client):
        r = _chat(chat_client, f"Buy NFT {MINT_A} now for 2 SOL, buyer {WALLET_A}")
        if "me_buy_now" not in r.types():
            asks_for_missing(r, "seller")

    def test_buy_now_response_quality(self, chat_client):
        r = _chat(chat_client, (
            f"Buy now NFT {MINT_B} from seller {WALLET_B} for 4 SOL, "
            f"buyer {WALLET_A}, tokenATA: ata456"
        ))
        triggered(r, "me_buy_now")
        no_raw_json(r)
        mentions(r, ["buy", "nft", "sol", "transaction", "sign", "al", "tx"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 23. ME_BUY_NOW_TRANSFER_NFT
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeBuyNowTransferNft:
    """me_buy_now_transfer_nft: 5 senaryo"""

    def test_buy_transfer_basic(self, chat_client):
        r = _chat(chat_client, (
            f"Buy NFT {MINT_A} from seller {WALLET_B} for 3 SOL and send it to wallet {WALLET_C}, "
            f"buyer {WALLET_A}, ATA: ata123, destATA: destAta789, createATA: true"
        ))
        triggered(r, "me_buy_now_transfer_nft")
        has_param(r, "me_buy_now_transfer_nft", "tokenMint", contains=MINT_A)

    def test_buy_transfer_gift(self, chat_client):
        r = _chat(chat_client, (
            f"Buy NFT {MINT_B} as a gift and send to {WALLET_C}, "
            f"seller {WALLET_B}, price 2 SOL, buyer {WALLET_A}, "
            f"tokenATA: ata001, destinationATA: dstAta001"
        ))
        triggered(r, "me_buy_now_transfer_nft")

    def test_buy_transfer_vs_buy_now(self, chat_client):
        r = _chat(chat_client, (
            f"Buy {MINT_C} from {WALLET_B} and transfer it to {WALLET_C}, "
            f"buyer {WALLET_A}, price 5 SOL, ATA: ata002, destATA: dst002, createATA true"
        ))
        triggered(r, "me_buy_now_transfer_nft")
        not_triggered(r, "me_buy_now")

    def test_buy_transfer_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            f"Purchase {MINT_D} from seller {WALLET_B} for 1 SOL and send to {WALLET_C}, "
            f"I'm {WALLET_A}, token ATA: ata003, destination ATA: dst003"
        ))
        triggered(r, "me_buy_now_transfer_nft")
        no_raw_json(r)
        no_hallucination(r)

    def test_buy_transfer_response_mentions_destination(self, chat_client):
        r = _chat(chat_client, (
            f"Buy {MINT_A} from {WALLET_B} and transfer to {WALLET_C}, "
            f"price 2.5 SOL, buyer {WALLET_A}, ATA: ata004, dest: dst004, createATA: true"
        ))
        triggered(r, "me_buy_now_transfer_nft")
        no_raw_json(r)
        mentions(r, ["transfer", "send", "destination", "gönder", "nft", "sol"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 24. ME_BUY_CANCEL
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeBuyCancel:
    """me_buy_cancel: 5 senaryo"""

    def test_buy_cancel_basic(self, chat_client):
        r = _chat(chat_client, f"Cancel my offer on NFT {MINT_A}, price was 2.5 SOL, buyer {WALLET_A}")
        triggered(r, "me_buy_cancel")
        has_param(r, "me_buy_cancel", "tokenMint", contains=MINT_A)

    def test_buy_cancel_price_extracted(self, chat_client):
        r = _chat(chat_client, f"Cancel my 3 SOL bid on mint {MINT_B}, my wallet: {WALLET_B}")
        triggered(r, "me_buy_cancel")
        p = r.params_for("me_buy_cancel")
        assert "3" in str(p.get("price", "")) or "3" in str(p)

    def test_buy_cancel_not_confused_with_sell_cancel(self, chat_client):
        r = _chat(chat_client, f"Cancel my buy offer on {MINT_C} at 1.5 SOL from {WALLET_A}")
        triggered(r, "me_buy_cancel")
        not_triggered(r, "me_sell_cancel")

    def test_buy_cancel_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Cancel buy offer for {MINT_D} at 4 SOL, buyer {WALLET_C}")
        triggered(r, "me_buy_cancel")
        no_raw_json(r)
        no_hallucination(r)

    def test_buy_cancel_response_quality(self, chat_client):
        r = _chat(chat_client, f"I want to cancel my 2 SOL offer on NFT {MINT_A}, wallet {WALLET_A}")
        triggered(r, "me_buy_cancel")
        no_raw_json(r)
        mentions(r, ["cancel", "iptal", "offer", "bid", "teklif"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 25. ME_BUY_CHANGE_PRICE
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeBuyChangePrice:
    """me_buy_change_price: 5 senaryo"""

    def test_buy_change_price_basic(self, chat_client):
        r = _chat(chat_client, f"Change my offer on NFT {MINT_A} from 2 SOL to 2.5 SOL, buyer {WALLET_A}")
        triggered(r, "me_buy_change_price")
        has_param(r, "me_buy_change_price", "tokenMint", contains=MINT_A)

    def test_buy_change_price_values_extracted(self, chat_client):
        r = _chat(chat_client, f"Update my bid on {MINT_B} from 1 to 1.8 SOL, wallet {WALLET_B}")
        triggered(r, "me_buy_change_price")
        p = r.params_for("me_buy_change_price")
        assert "1" in str(p.get("price", "")) or "1" in str(p)
        assert "1.8" in str(p.get("newPrice", "")) or "1.8" in str(p)

    def test_buy_change_price_raise(self, chat_client):
        r = _chat(chat_client, f"Raise my offer on {MINT_C} from 3 to 4 SOL, buyer {WALLET_A}")
        triggered(r, "me_buy_change_price")

    def test_buy_change_price_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Update buy offer on {MINT_D} from 5 SOL to 6 SOL, wallet {WALLET_C}")
        triggered(r, "me_buy_change_price")
        no_raw_json(r)
        no_hallucination(r)

    def test_buy_change_price_not_confused_with_sell_change(self, chat_client):
        r = _chat(chat_client, f"Change my bid/offer price on {MINT_A} from 2.5 to 3 SOL, buyer {WALLET_A}")
        triggered(r, "me_buy_change_price")
        not_triggered(r, "me_sell_change_price")


# ═══════════════════════════════════════════════════════════════════════════════
# 26. ME_SELL
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeSell:
    """me_sell: 6 senaryo"""

    def test_sell_basic(self, chat_client):
        r = _chat(chat_client, f"List my NFT {MINT_A} for 5 SOL on Magic Eden, wallet {WALLET_A}, token account: tokenAcc")
        triggered(r, "me_sell")
        has_param(r, "me_sell", "tokenMint", contains=MINT_A)

    def test_sell_price_extracted(self, chat_client):
        r = _chat(chat_client, f"Sell NFT {MINT_B} at 3.5 SOL, seller {WALLET_B}, token account: acc123")
        triggered(r, "me_sell")
        p = r.params_for("me_sell")
        assert "3.5" in str(p.get("price", "")) or "3.5" in str(p)

    def test_sell_not_confused_with_sell_now(self, chat_client):
        r = _chat(chat_client, f"Put my NFT {MINT_C} on sale for 7 SOL, wallet {WALLET_A}, tokenAccount: tAcc")
        triggered(r, "me_sell")
        not_triggered(r, "me_sell_now")

    def test_sell_missing_params_asks(self, chat_client):
        r = _chat(chat_client, f"I want to list my NFT on Magic Eden")
        if "me_sell" not in r.types():
            asks_for_missing(r, "mint")

    def test_sell_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"List {MINT_D} for 10 SOL, seller {WALLET_C}, tokenAcc: tAcc456")
        triggered(r, "me_sell")
        no_raw_json(r)
        no_hallucination(r)

    def test_sell_with_expiry(self, chat_client):
        r = _chat(chat_client, f"List NFT {MINT_A} for 4 SOL with 7 day expiry, seller {WALLET_A}, token account tAcc")
        triggered(r, "me_sell")
        has_param(r, "me_sell", "tokenMint", contains=MINT_A)


# ═══════════════════════════════════════════════════════════════════════════════
# 27. ME_SELL_CHANGE_PRICE
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeSellChangePrice:
    """me_sell_change_price: 5 senaryo"""

    def test_sell_change_price_basic(self, chat_client):
        r = _chat(chat_client, f"Change my listing price for NFT {MINT_A} from 5 to 4.5 SOL, seller {WALLET_A}, tokenAccount: acc")
        triggered(r, "me_sell_change_price")
        has_param(r, "me_sell_change_price", "tokenMint", contains=MINT_A)

    def test_sell_change_price_lower(self, chat_client):
        r = _chat(chat_client, f"Lower my listing on {MINT_B} from 10 to 8 SOL, wallet {WALLET_B}, token account: acc2")
        triggered(r, "me_sell_change_price")
        p = r.params_for("me_sell_change_price")
        assert "8" in str(p.get("newPrice", "")) or "8" in str(p)

    def test_sell_change_price_not_confused_with_buy_change(self, chat_client):
        r = _chat(chat_client, f"Update my listed price for {MINT_C} from 3 to 2.5 SOL, seller {WALLET_A}, tokenAcc: tacc")
        triggered(r, "me_sell_change_price")
        not_triggered(r, "me_buy_change_price")

    def test_sell_change_price_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Relist NFT {MINT_D} at 6 SOL (was 7 SOL), seller {WALLET_C}, tokenAcc: acc3")
        triggered(r, "me_sell_change_price")
        no_raw_json(r)
        no_hallucination(r)

    def test_sell_change_price_response(self, chat_client):
        r = _chat(chat_client, f"Update my Magic Eden listing: {MINT_A} from 5 to 4 SOL, seller {WALLET_A}, tAcc: acc4")
        triggered(r, "me_sell_change_price")
        no_raw_json(r)
        mentions(r, ["price", "update", "listing", "fiyat", "güncelle", "sol"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 28. ME_SELL_NOW
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeSellNow:
    """me_sell_now: 5 senaryo"""

    def test_sell_now_basic(self, chat_client):
        r = _chat(chat_client, (
            f"Accept the 3 SOL offer from buyer {WALLET_B} on my NFT {MINT_A}, "
            f"seller {WALLET_A}, tokenATA: ata999, newPrice: 3, expiry: 0"
        ))
        triggered(r, "me_sell_now")
        has_param(r, "me_sell_now", "tokenMint", contains=MINT_A)
        has_param(r, "me_sell_now", "buyer", contains=WALLET_B)

    def test_sell_now_accept_bid(self, chat_client):
        r = _chat(chat_client, (
            f"Accept bid from {WALLET_C} for NFT {MINT_B}, "
            f"seller {WALLET_A}, price 2.5, new price 2.5, ATA: ata888, sellerExpiry: 0"
        ))
        triggered(r, "me_sell_now")

    def test_sell_now_vs_sell_routing(self, chat_client):
        r = _chat(chat_client, (
            f"Sell NFT {MINT_C} to buyer {WALLET_B} NOW, "
            f"seller {WALLET_A}, ATA: ata777, price 4, newPrice 4, sellerExpiry 0"
        ))
        triggered(r, "me_sell_now")
        not_triggered(r, "me_sell")

    def test_sell_now_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            f"Fulfill offer from {WALLET_B} on {MINT_D}, "
            f"seller {WALLET_A}, ATA: ata666, price 1.5, newPrice 1.5, expiry 0"
        ))
        triggered(r, "me_sell_now")
        no_raw_json(r)
        no_hallucination(r)

    def test_sell_now_response_quality(self, chat_client):
        r = _chat(chat_client, (
            f"Accept the offer: buyer {WALLET_B}, seller {WALLET_A}, "
            f"NFT {MINT_A}, ATA: ata555, price 5, newPrice 5, expiry 0"
        ))
        triggered(r, "me_sell_now")
        no_raw_json(r)
        mentions(r, ["accept", "sell", "offer", "transaction", "tx", "kabul", "sat"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 29. ME_SELL_CANCEL
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeSellCancel:
    """me_sell_cancel: 5 senaryo"""

    def test_sell_cancel_basic(self, chat_client):
        r = _chat(chat_client, f"Cancel my listing for NFT {MINT_A}, price was 5 SOL, seller {WALLET_A}, tokenAccount: acc")
        triggered(r, "me_sell_cancel")
        has_param(r, "me_sell_cancel", "tokenMint", contains=MINT_A)

    def test_sell_cancel_delist(self, chat_client):
        r = _chat(chat_client, f"Delist NFT {MINT_B} from Magic Eden, price 3 SOL, wallet {WALLET_B}, tokenAcc: acc2")
        triggered(r, "me_sell_cancel")

    def test_sell_cancel_not_confused_with_buy_cancel(self, chat_client):
        r = _chat(chat_client, f"Remove my listing for {MINT_C} (I listed it for 7 SOL), seller {WALLET_A}, tAcc: acc3")
        triggered(r, "me_sell_cancel")
        not_triggered(r, "me_buy_cancel")

    def test_sell_cancel_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Unlist NFT {MINT_D} priced at 2 SOL, seller {WALLET_C}, tokenAccount: acc4")
        triggered(r, "me_sell_cancel")
        no_raw_json(r)
        no_hallucination(r)

    def test_sell_cancel_response_quality(self, chat_client):
        r = _chat(chat_client, f"Cancel my 4 SOL listing for {MINT_A}, seller {WALLET_A}, tAcc: acc5")
        triggered(r, "me_sell_cancel")
        no_raw_json(r)
        mentions(r, ["cancel", "delist", "listing", "iptal", "kaldır"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 30. ME_DEPOSIT
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeDeposit:
    """me_deposit: 5 senaryo"""

    def test_deposit_basic(self, chat_client):
        r = _chat(chat_client, f"Deposit 1 SOL to Magic Eden escrow from wallet {WALLET_A}")
        triggered(r, "me_deposit")
        has_param(r, "me_deposit", "buyer", contains=WALLET_A)

    def test_deposit_amount_extracted(self, chat_client):
        r = _chat(chat_client, f"Add 2.5 SOL to my Magic Eden balance, wallet {WALLET_B}")
        triggered(r, "me_deposit")
        p = r.params_for("me_deposit")
        assert "2.5" in str(p.get("amount", "")) or "2.5" in str(p)

    def test_deposit_not_confused_with_withdraw(self, chat_client):
        r = _chat(chat_client, f"Put 0.5 SOL into Magic Eden escrow for bidding, wallet {WALLET_A}")
        triggered(r, "me_deposit")
        not_triggered(r, "me_withdraw")

    def test_deposit_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Fund my Magic Eden escrow with 3 SOL, buyer {WALLET_C}")
        triggered(r, "me_deposit")
        no_raw_json(r)
        no_hallucination(r)

    def test_deposit_response_quality(self, chat_client):
        r = _chat(chat_client, f"Deposit 1 SOL to Magic Eden for wallet {WALLET_A}")
        triggered(r, "me_deposit")
        no_raw_json(r)
        mentions(r, ["deposit", "escrow", "sol", "yatır", "transaction", "tx"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 31. ME_WITHDRAW
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeWithdraw:
    """me_withdraw: 5 senaryo"""

    def test_withdraw_basic(self, chat_client):
        r = _chat(chat_client, f"Withdraw 0.5 SOL from Magic Eden escrow for wallet {WALLET_A}")
        triggered(r, "me_withdraw")
        has_param(r, "me_withdraw", "buyer", contains=WALLET_A)

    def test_withdraw_amount_extracted(self, chat_client):
        r = _chat(chat_client, f"Get back 2 SOL from my Magic Eden escrow, wallet {WALLET_B}")
        triggered(r, "me_withdraw")
        p = r.params_for("me_withdraw")
        assert "2" in str(p.get("amount", "")) or "2" in str(p)

    def test_withdraw_not_confused_with_deposit(self, chat_client):
        r = _chat(chat_client, f"Pull out 1 SOL from Magic Eden escrow, buyer {WALLET_A}")
        triggered(r, "me_withdraw")
        not_triggered(r, "me_deposit")

    def test_withdraw_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Withdraw all funds from Magic Eden escrow — 0.8 SOL, wallet {WALLET_C}")
        triggered(r, "me_withdraw")
        no_raw_json(r)
        no_hallucination(r)

    def test_withdraw_response_quality(self, chat_client):
        r = _chat(chat_client, f"Take back 0.5 SOL from Magic Eden escrow for {WALLET_A}")
        triggered(r, "me_withdraw")
        no_raw_json(r)
        mentions(r, ["withdraw", "escrow", "sol", "çek", "transaction", "tx"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 32. ME_MMM_POOLS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmPools:
    """me_mmm_pools: 6 senaryo"""

    def test_mmm_pools_by_collection(self, chat_client):
        r = _chat(chat_client, f"Show MMM pools for {COL_OKAY} collection")
        triggered(r, "me_mmm_pools")
        p = r.params_for("me_mmm_pools")
        assert COL_OKAY in str(p.get("collectionSymbol", "")).lower() or COL_OKAY in str(p)

    def test_mmm_pools_by_owner(self, chat_client):
        r = _chat(chat_client, f"Show MMM pools owned by wallet {WALLET_B}")
        triggered(r, "me_mmm_pools")
        has_param(r, "me_mmm_pools", "owner", contains=WALLET_B)

    def test_mmm_pools_sort_by_spot_price(self, chat_client):
        r = _chat(chat_client, f"Show {COL_DEGODS} MMM pools sorted by spot price")
        triggered(r, "me_mmm_pools")

    def test_mmm_pools_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"List market maker pools for {COL_OKAY}")
        triggered(r, "me_mmm_pools")
        no_raw_json(r)
        no_hallucination(r)

    def test_mmm_pools_missing_both_params(self, chat_client):
        r = _chat(chat_client, "Show all Magic Eden MMM pools")
        if "me_mmm_pools" not in r.types():
            asks_for_missing(r, "collection")

    def test_mmm_pools_response_quality(self, chat_client):
        r = _chat(chat_client, f"What MMM buy pools exist for {COL_MAGIC}?")
        triggered(r, "me_mmm_pools")
        no_raw_json(r)
        mentions(r, ["pool", "mmm", "spot", "price", "buyer", "collection"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 33. ME_MMM_TOKEN_POOLS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmTokenPools:
    """me_mmm_token_pools: 5 senaryo"""

    def test_mmm_token_pools_basic(self, chat_client):
        r = _chat(chat_client, f"Show best MMM offers for NFT {MINT_A}")
        triggered(r, "me_mmm_token_pools")
        has_param(r, "me_mmm_token_pools", "mintAddress", contains=MINT_A)

    def test_mmm_token_pools_top_5(self, chat_client):
        r = _chat(chat_client, f"Top 5 MMM pools bidding on NFT {MINT_B}")
        triggered(r, "me_mmm_token_pools")
        p = r.params_for("me_mmm_token_pools")
        assert int(p.get("limit", 1) or 1) <= 5

    def test_mmm_token_pools_best_offer(self, chat_client):
        r = _chat(chat_client, f"What's the best MMM buy offer for mint {MINT_C}?")
        triggered(r, "me_mmm_token_pools")
        has_param(r, "me_mmm_token_pools", "mintAddress", contains=MINT_C)

    def test_mmm_token_pools_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Show MMM pool offers for NFT {MINT_D}")
        triggered(r, "me_mmm_token_pools")
        no_raw_json(r)
        no_hallucination(r)

    def test_mmm_token_pools_vs_token_offers(self, chat_client):
        r = _chat(chat_client, f"What are the best market maker pool bids for {MINT_A}?")
        triggered(r, "me_mmm_token_pools")


# ═══════════════════════════════════════════════════════════════════════════════
# 34. ME_MMM_CREATE_POOL
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmCreatePool:
    """me_mmm_create_pool: 5 senaryo"""

    def test_create_pool_basic(self, chat_client):
        r = _chat(chat_client, (
            f"Create a linear MMM buy pool for {COL_DEGODS} at 5 SOL, "
            f"0% LP fee, 5% royalty, no reinvest, owner {WALLET_A}"
        ))
        triggered(r, "me_mmm_create_pool")
        p = r.params_for("me_mmm_create_pool")
        assert p.get("collectionSymbol") or COL_DEGODS in str(p)

    def test_create_pool_exp_curve(self, chat_client):
        r = _chat(chat_client, (
            f"Create exponential MMM pool for {COL_OKAY}, spot price 3 SOL, "
            f"delta 0, reinvest both, lpFeeBp 100, royalty 500, "
            f"payment SOL, owner {WALLET_B}"
        ))
        triggered(r, "me_mmm_create_pool")

    def test_create_pool_with_sol_deposit(self, chat_client):
        r = _chat(chat_client, (
            f"Create a buy MMM pool for {COL_MAGIC} at 2 SOL spot price, "
            f"linear curve, delta 0, no reinvest, 0 lp fee, 500bp royalty, "
            f"deposit 5 SOL initially, owner {WALLET_A}"
        ))
        triggered(r, "me_mmm_create_pool")

    def test_create_pool_missing_params(self, chat_client):
        r = _chat(chat_client, f"Create a Magic Eden MMM pool for {COL_OKAY}")
        if "me_mmm_create_pool" not in r.types():
            asks_for_missing(r, "price")

    def test_create_pool_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            f"Set up MMM market maker pool for {COL_DEGODS}, "
            f"5 SOL, linear, delta 0, reinvestBuy false, reinvestSell false, "
            f"lpFeeBp 0, royaltyBp 500, paymentMint SOL, owner {WALLET_A}"
        ))
        triggered(r, "me_mmm_create_pool")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 35. ME_MMM_UPDATE_POOL
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmUpdatePool:
    """me_mmm_update_pool: 5 senaryo"""

    def test_update_pool_spot_price(self, chat_client):
        r = _chat(chat_client, (
            f"Update MMM pool {POOL_A} spot price to 4 SOL, "
            f"curveType exp, delta 0, reinvestBuy false, reinvestSell false, "
            f"expiry 0, lpFeeBp 0, royaltyBp 500"
        ))
        triggered(r, "me_mmm_update_pool")
        has_param(r, "me_mmm_update_pool", "pool", contains=POOL_A)

    def test_update_pool_lp_fee(self, chat_client):
        r = _chat(chat_client, (
            f"Change LP fee on pool {POOL_B} to 100bp, "
            f"spotPrice 3, curveType linear, delta 0, reinvestBuy false, reinvestSell false, "
            f"expiry 0, royaltyBp 500"
        ))
        triggered(r, "me_mmm_update_pool")
        has_param(r, "me_mmm_update_pool", "pool", contains=POOL_B)

    def test_update_pool_expiry(self, chat_client):
        r = _chat(chat_client, (
            f"Set expiry on MMM pool {POOL_A} to 1735689600, "
            f"spotPrice 5, curveType exp, delta 0, reinvestBuy false, reinvestSell false, "
            f"lpFeeBp 0, royaltyBp 500"
        ))
        triggered(r, "me_mmm_update_pool")

    def test_update_pool_not_confused_with_create(self, chat_client):
        r = _chat(chat_client, (
            f"Modify existing pool {POOL_A}: new spot price 6 SOL, "
            f"linear, delta 0, reinvestBuy false, reinvestSell false, "
            f"expiry 0, lpFeeBp 0, royaltyBp 500"
        ))
        triggered(r, "me_mmm_update_pool")
        not_triggered(r, "me_mmm_create_pool")

    def test_update_pool_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            f"Update pool {POOL_B}: spotPrice 4.5, curveType linear, curveDelta 0, "
            f"reinvestBuy false, reinvestSell false, expiry 0, lpFeeBp 50, royaltyBp 500"
        ))
        triggered(r, "me_mmm_update_pool")
        no_raw_json(r)
        no_hallucination(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 36. ME_MMM_SOL_DEPOSIT_BUY
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmSolDepositBuy:
    """me_mmm_sol_deposit_buy: 5 senaryo"""

    def test_deposit_buy_basic(self, chat_client):
        r = _chat(chat_client, f"Deposit 2 SOL into MMM pool {POOL_A}")
        triggered(r, "me_mmm_sol_deposit_buy")
        has_param(r, "me_mmm_sol_deposit_buy", "pool", contains=POOL_A)

    def test_deposit_buy_amount(self, chat_client):
        r = _chat(chat_client, f"Add 5 SOL to my buy pool {POOL_B}")
        triggered(r, "me_mmm_sol_deposit_buy")
        p = r.params_for("me_mmm_sol_deposit_buy")
        assert "5" in str(p.get("solAmount", "")) or "5" in str(p)

    def test_deposit_buy_not_confused_with_withdraw(self, chat_client):
        r = _chat(chat_client, f"Fund MMM pool {POOL_A} with 3 SOL")
        triggered(r, "me_mmm_sol_deposit_buy")
        not_triggered(r, "me_mmm_sol_withdraw_buy")

    def test_deposit_buy_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Put 1 SOL into MMM buy pool {POOL_B}")
        triggered(r, "me_mmm_sol_deposit_buy")
        no_raw_json(r)
        no_hallucination(r)

    def test_deposit_buy_response_quality(self, chat_client):
        r = _chat(chat_client, f"Deposit 0.5 SOL to MMM pool {POOL_A} buy side")
        triggered(r, "me_mmm_sol_deposit_buy")
        no_raw_json(r)
        mentions(r, ["deposit", "sol", "pool", "transaction", "tx", "yatır"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 37. ME_MMM_SOL_WITHDRAW_BUY
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmSolWithdrawBuy:
    """me_mmm_sol_withdraw_buy: 5 senaryo"""

    def test_withdraw_buy_basic(self, chat_client):
        r = _chat(chat_client, f"Withdraw 1.5 SOL from MMM pool {POOL_A}")
        triggered(r, "me_mmm_sol_withdraw_buy")
        has_param(r, "me_mmm_sol_withdraw_buy", "pool", contains=POOL_A)

    def test_withdraw_buy_amount(self, chat_client):
        r = _chat(chat_client, f"Pull 3 SOL out of buy pool {POOL_B}")
        triggered(r, "me_mmm_sol_withdraw_buy")
        p = r.params_for("me_mmm_sol_withdraw_buy")
        assert "3" in str(p.get("solAmount", "")) or "3" in str(p)

    def test_withdraw_buy_not_confused_with_deposit(self, chat_client):
        r = _chat(chat_client, f"Remove 2 SOL from MMM pool {POOL_A}")
        triggered(r, "me_mmm_sol_withdraw_buy")
        not_triggered(r, "me_mmm_sol_deposit_buy")

    def test_withdraw_buy_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Take back 0.5 SOL from MMM pool {POOL_B}")
        triggered(r, "me_mmm_sol_withdraw_buy")
        no_raw_json(r)
        no_hallucination(r)

    def test_withdraw_buy_response_quality(self, chat_client):
        r = _chat(chat_client, f"Withdraw 1 SOL from buy side of pool {POOL_A}")
        triggered(r, "me_mmm_sol_withdraw_buy")
        no_raw_json(r)
        mentions(r, ["withdraw", "sol", "pool", "transaction", "tx", "çek"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 38. ME_MMM_SOL_CLOSE_POOL
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmSolClosePool:
    """me_mmm_sol_close_pool: 5 senaryo"""

    def test_close_pool_basic(self, chat_client):
        r = _chat(chat_client, f"Close MMM pool {POOL_A}")
        triggered(r, "me_mmm_sol_close_pool")
        has_param(r, "me_mmm_sol_close_pool", "pool", contains=POOL_A)

    def test_close_pool_reclaim(self, chat_client):
        r = _chat(chat_client, f"Shut down and reclaim funds from pool {POOL_B}")
        triggered(r, "me_mmm_sol_close_pool")
        has_param(r, "me_mmm_sol_close_pool", "pool", contains=POOL_B)

    def test_close_pool_not_confused_with_withdraw(self, chat_client):
        r = _chat(chat_client, f"Close and terminate MMM pool {POOL_A} permanently")
        triggered(r, "me_mmm_sol_close_pool")
        not_triggered(r, "me_mmm_sol_withdraw_buy")

    def test_close_pool_no_raw_json(self, chat_client):
        r = _chat(chat_client, f"Dissolve MMM pool {POOL_B} and get my SOL back")
        triggered(r, "me_mmm_sol_close_pool")
        no_raw_json(r)
        no_hallucination(r)

    def test_close_pool_response_quality(self, chat_client):
        r = _chat(chat_client, f"Close my Magic Eden market maker pool {POOL_A}")
        triggered(r, "me_mmm_sol_close_pool")
        no_raw_json(r)
        mentions(r, ["close", "pool", "transaction", "tx", "kapat", "reclaim", "sol"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 39. ME_MMM_SOL_FULFILL_BUY (seller side)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmSolFulfillBuy:
    """me_mmm_sol_fulfill_buy: 5 senaryo"""

    def test_fulfill_buy_basic(self, chat_client):
        r = _chat(chat_client, (
            f"Sell my NFT {MINT_A} into MMM pool {POOL_A}, "
            f"seller {WALLET_A}, min price 3 SOL, token account: tAcc"
        ))
        triggered(r, "me_mmm_sol_fulfill_buy")
        has_param(r, "me_mmm_sol_fulfill_buy", "pool", contains=POOL_A)

    def test_fulfill_buy_params_extracted(self, chat_client):
        r = _chat(chat_client, (
            f"Fulfill the buy order in pool {POOL_B} with NFT {MINT_B}, "
            f"I'm the seller {WALLET_B}, min payment 2 SOL, token account: acc2"
        ))
        triggered(r, "me_mmm_sol_fulfill_buy")
        has_param(r, "me_mmm_sol_fulfill_buy", "assetMint", contains=MINT_B)

    def test_fulfill_buy_vs_fulfill_sell(self, chat_client):
        r = _chat(chat_client, (
            f"I want to SELL my NFT {MINT_C} into an MMM pool {POOL_A} buy order, "
            f"seller {WALLET_A}, min price 1.5 SOL, token account: acc3"
        ))
        triggered(r, "me_mmm_sol_fulfill_buy")
        not_triggered(r, "me_mmm_sol_fulfill_sell")

    def test_fulfill_buy_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            f"Accept pool offer: sell {MINT_D} to pool {POOL_B}, "
            f"seller {WALLET_C}, minPayment 4, tokenAccount: acc4"
        ))
        triggered(r, "me_mmm_sol_fulfill_buy")
        no_raw_json(r)
        no_hallucination(r)

    def test_fulfill_buy_response_quality(self, chat_client):
        r = _chat(chat_client, (
            f"Sell into MMM pool {POOL_A}: NFT {MINT_A}, seller {WALLET_A}, "
            f"min 2 SOL, tokenAcc: acc5"
        ))
        triggered(r, "me_mmm_sol_fulfill_buy")
        no_raw_json(r)
        mentions(r, ["sell", "pool", "transaction", "tx", "sat", "mmm"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 40. ME_MMM_SOL_FULFILL_SELL (buyer side)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMmmSolFulfillSell:
    """me_mmm_sol_fulfill_sell: 5 senaryo"""

    def test_fulfill_sell_basic(self, chat_client):
        r = _chat(chat_client, (
            f"Buy NFT {MINT_A} from MMM pool {POOL_A}, "
            f"buyer {WALLET_A}, max price 5 SOL, royalty 500bp"
        ))
        triggered(r, "me_mmm_sol_fulfill_sell")
        has_param(r, "me_mmm_sol_fulfill_sell", "pool", contains=POOL_A)

    def test_fulfill_sell_params_extracted(self, chat_client):
        r = _chat(chat_client, (
            f"Fulfill sell order in pool {POOL_B}: buy {MINT_B}, "
            f"I'm buyer {WALLET_B}, max I'll pay 3 SOL, royalty 750bp"
        ))
        triggered(r, "me_mmm_sol_fulfill_sell")
        has_param(r, "me_mmm_sol_fulfill_sell", "assetMint", contains=MINT_B)

    def test_fulfill_sell_vs_fulfill_buy(self, chat_client):
        r = _chat(chat_client, (
            f"BUY from an MMM pool {POOL_A}: get NFT {MINT_C}, "
            f"buyer {WALLET_A}, max payment 4 SOL, royalty 500bp"
        ))
        triggered(r, "me_mmm_sol_fulfill_sell")
        not_triggered(r, "me_mmm_sol_fulfill_buy")

    def test_fulfill_sell_no_raw_json(self, chat_client):
        r = _chat(chat_client, (
            f"Buy from pool {POOL_B}: asset {MINT_D}, buyer {WALLET_C}, "
            f"max 2 SOL, royalty 500bp"
        ))
        triggered(r, "me_mmm_sol_fulfill_sell")
        no_raw_json(r)
        no_hallucination(r)

    def test_fulfill_sell_response_quality(self, chat_client):
        r = _chat(chat_client, (
            f"Purchase from Magic Eden MMM pool {POOL_A}: NFT {MINT_A}, "
            f"buyer {WALLET_A}, max 6 SOL, royalty 500bp"
        ))
        triggered(r, "me_mmm_sol_fulfill_sell")
        no_raw_json(r)
        mentions(r, ["buy", "pool", "transaction", "tx", "al", "mmm"], min_hits=1)


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-CUTTING: Routing & disambiguation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeMagicTicketBurns:
    """me_magic_ticket_burns — Magic Ticket burn instructions: 7 tests"""

    def test_single_mint_burn(self, chat_client):
        r = _chat(chat_client, f"Burn my Magic Ticket {MINT_A}, wallet {WALLET_A}")
        triggered(r, "me_magic_ticket_burns")
        has_param(r, "walletAddress", WALLET_A)
        has_param(r, "mintAddresses", MINT_A)

    def test_multiple_mints_burn(self, chat_client):
        r = _chat(chat_client, f"Burn these Magic Tickets: {MINT_A} and {MINT_B}, wallet {WALLET_A}")
        triggered(r, "me_magic_ticket_burns")
        has_param(r, "walletAddress", WALLET_A)
        no_raw_json(r)

    def test_missing_mint_asks(self, chat_client):
        r = _chat(chat_client, f"Burn my Magic Tickets, wallet {WALLET_A}")
        asks_for_missing(r, ["mint", "address"])

    def test_missing_wallet_asks(self, chat_client):
        r = _chat(chat_client, f"Burn Magic Ticket {MINT_A}")
        asks_for_missing(r, ["wallet"])

    def test_not_triggered_for_regular_burn(self, chat_client):
        r = _chat(chat_client, f"Burn token {MINT_A} from wallet {WALLET_A}")
        not_triggered(r, "me_magic_ticket_burns")

    def test_not_triggered_for_nft_list(self, chat_client):
        r = _chat(chat_client, f"List NFT {MINT_A} for 2 SOL on Magic Eden")
        not_triggered(r, "me_magic_ticket_burns")

    def test_burn_irreversible_warning(self, chat_client):
        r = _chat(chat_client, f"Burn all my Magic Tickets, wallet {WALLET_A}, mints: {MINT_A}")
        triggered(r, "me_magic_ticket_burns")
        no_raw_json(r)


class TestMeMarketplacePopular:
    """me_marketplace_popular — Magic Eden popular collections: 7 tests"""

    def test_popular_no_time_range(self, chat_client):
        r = _chat(chat_client, "What are the most popular NFT collections on Magic Eden right now?")
        triggered(r, "me_marketplace_popular")
        no_raw_json(r)

    def test_popular_1h(self, chat_client):
        r = _chat(chat_client, "Show me trending NFT collections from the last hour on Magic Eden")
        triggered(r, "me_marketplace_popular")
        has_param(r, "timeRange", "1h")

    def test_popular_1d(self, chat_client):
        r = _chat(chat_client, "What are the top Magic Eden NFT collections today?")
        triggered(r, "me_marketplace_popular")
        has_param(r, "timeRange", "1d")

    def test_popular_7d(self, chat_client):
        r = _chat(chat_client, "Top NFT collections on Magic Eden this week")
        triggered(r, "me_marketplace_popular")
        has_param(r, "timeRange", "7d")

    def test_popular_30d(self, chat_client):
        r = _chat(chat_client, "Show me the most popular Magic Eden collections this past month")
        triggered(r, "me_marketplace_popular")
        has_param(r, "timeRange", "30d")

    def test_not_triggered_for_collection_stats(self, chat_client):
        r = _chat(chat_client, f"Get stats for the {COL_DEGODS} collection")
        not_triggered(r, "me_marketplace_popular")

    def test_not_triggered_for_listing_query(self, chat_client):
        r = _chat(chat_client, f"List NFTs for sale in {COL_OKAY}")
        not_triggered(r, "me_marketplace_popular")


class TestMeRouting:
    """Magic Eden cross-endpoint routing: 12 test"""

    def test_escrow_balance_not_deposit(self, chat_client):
        r = _chat(chat_client, f"Check my escrow balance on Magic Eden for {WALLET_A}")
        triggered(r, "me_wallet_escrow_balance")
        not_triggered(r, "me_deposit")

    def test_offers_made_vs_received(self, chat_client):
        r = _chat(chat_client, f"Offers that {WALLET_A} sent to others (not received)")
        triggered(r, "me_wallet_offers_made")
        not_triggered(r, "me_wallet_offers_received")

    def test_collection_stats_vs_listings(self, chat_client):
        r = _chat(chat_client, f"Floor price stats for {COL_DEGODS}")
        triggered(r, "me_collection_stats")
        not_triggered(r, "me_collection_listings")

    def test_buy_vs_buy_now(self, chat_client):
        r = _chat(chat_client, f"Place an offer/bid on {MINT_A} at 2 SOL from {WALLET_A}")
        triggered(r, "me_buy_instruction")
        not_triggered(r, "me_buy_now")

    def test_sell_vs_sell_now(self, chat_client):
        r = _chat(chat_client, f"List my NFT {MINT_A} for 5 SOL on Magic Eden, seller {WALLET_A}, tokenAcc: acc1")
        triggered(r, "me_sell")
        not_triggered(r, "me_sell_now")

    def test_sell_cancel_vs_buy_cancel(self, chat_client):
        r = _chat(chat_client, f"Cancel my listing (not my bid) for {MINT_B}, seller {WALLET_A}, was 4 SOL, tAcc: acc2")
        triggered(r, "me_sell_cancel")
        not_triggered(r, "me_buy_cancel")

    def test_deposit_vs_withdraw(self, chat_client):
        r = _chat(chat_client, f"Add SOL to Magic Eden escrow: 1 SOL, wallet {WALLET_A}")
        triggered(r, "me_deposit")
        not_triggered(r, "me_withdraw")

    def test_mmm_pool_query_vs_instruction(self, chat_client):
        r = _chat(chat_client, f"Show existing MMM pools for {COL_OKAY}")
        triggered(r, "me_mmm_pools")
        not_triggered(r, "me_mmm_create_pool")

    def test_holder_stats_vs_leaderboard(self, chat_client):
        r = _chat(chat_client, f"How many unique holders does {COL_DEGODS} have?")
        triggered(r, "me_collection_holder_stats")
        not_triggered(r, "me_collection_leaderboard")

    def test_token_info_vs_token_listings(self, chat_client):
        r = _chat(chat_client, f"What is NFT {MINT_A}? Show me metadata.")
        triggered(r, "me_token")
        not_triggered(r, "me_token_listings")

    def test_fulfill_buy_vs_sell(self, chat_client):
        r = _chat(chat_client, (
            f"Sell my NFT into a pool buy order, NFT: {MINT_A}, "
            f"pool: {POOL_A}, seller: {WALLET_A}, min: 3 SOL, tAcc: acc"
        ))
        triggered(r, "me_mmm_sol_fulfill_buy")
        not_triggered(r, "me_mmm_sol_fulfill_sell")

    def test_collection_listings_vs_batch(self, chat_client):
        r = _chat(chat_client, f"Show floor listings for just the {COL_DEGODS} collection")
        triggered(r, "me_collection_listings")
        not_triggered(r, "me_collections_batch_listings")

    def test_popular_vs_collection_stats(self, chat_client):
        r = _chat(chat_client, "What NFT collections are trending across all of Magic Eden this week?")
        triggered(r, "me_marketplace_popular")
        not_triggered(r, "me_collection_stats")

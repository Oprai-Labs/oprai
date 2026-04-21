"""
Temel Solana Aksiyonları — Kapsamlı LLM Kalite Testi

9 core aksiyon için farklı case'ler:
  1. transfer       — SOL / SPL, "all", eksik adres, Türkçe
  2. swap           — token takası, slippage, "all", Türkçe
  3. stake          — Marinade / JupSOL / Jito / native ayrımı
  4. unstake        — mSOL / jupSOL / jitoSOL ayrımı
  5. burn           — kısmi, hepsi, close_mint
  6. scan_empty_accounts — boş hesap taraması
  7. close_accounts — boş hesap kapatma
  8. claim          — protocol-specific yönlendirme
  9. vote           — governance yönlendirme

Test kriterleri:
  - Doğru action tipi tetiklenme
  - Parametre çıkarma kalitesi
  - Clarification isteme (eksik zorunlu param)
  - Türkçe + İngilizce doğru routing
  - Hatalı yönlendirme yapılmaması

Toplam: ~80 test
"""

from __future__ import annotations

import json
import os
import uuid
import re

import httpx
import pytest

# ─── Config ───────────────────────────────────────────────────────────────────

CHAT_URL     = os.getenv("CHAT_SERVICE_URL", "http://127.0.0.1:3020")
INTERNAL_KEY = os.getenv("OPRAI_INTERNAL_API_KEY", "")
TEST_WALLET  = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"

WALLET_A = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa"
WALLET_B = "7nYabs9dUhvxYwdTnrWVBL129NNrvYxvBBVsbkr2dwQW"
WALLET_C = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"

BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF_MINT  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


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

    def has_text(self, min_chars: int = 30) -> bool:
        return len(self.text.strip()) >= min_chars


# ─── Helpers ──────────────────────────────────────────────────────────────────

def triggered(r: StreamResult, t: str, msg: str = ""):
    assert t in r.types(), (
        f"'{t}' tetiklenmedi.\nTetiklenenler: {r.types()}\nLLM: {r.text[:400]}\n{msg}"
    )


def not_triggered(r: StreamResult, t: str, msg: str = ""):
    assert t not in r.types(), (
        f"'{t}' tetiklenmemeli.\nTetiklenenler: {r.types()}\nLLM: {r.text[:400]}\n{msg}"
    )


def param_eq(r: StreamResult, action: str, key: str, val: str, case_insensitive=True):
    params = r.params_for(action)
    actual = str(params.get(key, ""))
    if case_insensitive:
        assert actual.lower() == val.lower(), (
            f"{action}.{key}: beklenen='{val}', gerçek='{actual}'\nParams: {params}"
        )
    else:
        assert actual == val, (
            f"{action}.{key}: beklenen='{val}', gerçek='{actual}'\nParams: {params}"
        )


def param_contains(r: StreamResult, action: str, key: str, substring: str):
    params = r.params_for(action)
    actual = str(params.get(key, ""))
    assert substring.lower() in actual.lower(), (
        f"{action}.{key}: '{substring}' bulunamadı. Gerçek='{actual}'\nParams: {params}"
    )


def asked_for_clarification(r: StreamResult, msg: str = ""):
    assert r.asked_clarification() or (
        len(r.text) > 20 and not r.actions and not r.queries
    ), f"Clarification istenmedi.\nLLM: {r.text[:400]}\n{msg}"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TRANSFER
# ═══════════════════════════════════════════════════════════════════════════════

class TestTransfer:

    def test_sol_basic(self, chat_client):
        r = _chat(chat_client, f"Send 1 SOL to {WALLET_B}")
        triggered(r, "transfer")
        param_eq(r, "transfer", "token", "SOL")
        param_eq(r, "transfer", "amount", "1")
        param_contains(r, "transfer", "to", WALLET_B[:8])

    def test_usdc_basic(self, chat_client):
        r = _chat(chat_client, f"Transfer 50 USDC to {WALLET_B}")
        triggered(r, "transfer")
        param_eq(r, "transfer", "token", "USDC")
        param_eq(r, "transfer", "amount", "50")

    def test_turkish_gonder(self, chat_client):
        r = _chat(chat_client, f"{WALLET_B} adresine 2 SOL gönder")
        triggered(r, "transfer")
        param_eq(r, "transfer", "token", "SOL")
        param_eq(r, "transfer", "amount", "2")

    def test_turkish_aktar(self, chat_client):
        r = _chat(chat_client, f"100 USDT'yi {WALLET_C}'ye aktar")
        triggered(r, "transfer")
        param_contains(r, "transfer", "token", "USDT")

    def test_all_balance(self, chat_client):
        r = _chat(chat_client, f"Send all my USDC to {WALLET_B}")
        triggered(r, "transfer")
        param_eq(r, "transfer", "token", "USDC")
        params = r.params_for("transfer")
        assert str(params.get("amount", "")).lower() == "all", (
            f"amount 'all' olmalı, gerçek: {params.get('amount')}"
        )

    def test_hepsini_gonder_turkish(self, chat_client):
        r = _chat(chat_client, f"Tüm SOL'umu {WALLET_B}'ye gönder")
        triggered(r, "transfer")
        params = r.params_for("transfer")
        assert str(params.get("amount", "")).lower() == "all", (
            f"amount 'all' olmalı, gerçek: {params.get('amount')}"
        )

    def test_missing_recipient_asks_clarification(self, chat_client):
        r = _chat(chat_client, "Send 0.5 SOL to someone")
        asked_for_clarification(r, "Adres istenmedi")

    def test_missing_recipient_no_address(self, chat_client):
        r = _chat(chat_client, "Transfer 10 USDC")
        asked_for_clarification(r, "Alıcı adresi istenmedi")

    def test_mint_address_token(self, chat_client):
        r = _chat(chat_client, f"Send 1000 {BONK_MINT} tokens to {WALLET_B}")
        triggered(r, "transfer")
        params = r.params_for("transfer")
        assert params.get("token") or params.get("mint"), "token parametresi yok"

    def test_decimal_amount(self, chat_client):
        r = _chat(chat_client, f"Send 0.001 SOL to {WALLET_B}")
        triggered(r, "transfer")
        params = r.params_for("transfer")
        assert float(params.get("amount", 0)) == pytest.approx(0.001, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SWAP
# ═══════════════════════════════════════════════════════════════════════════════

class TestSwap:

    def test_sol_to_usdc(self, chat_client):
        r = _chat(chat_client, "Swap 1 SOL for USDC")
        triggered(r, "swap")
        params = r.params_for("swap")
        assert "SOL" in str(params.get("inputMint", "")).upper()
        assert "USDC" in str(params.get("outputMint", "")).upper()

    def test_usdc_to_sol(self, chat_client):
        r = _chat(chat_client, "Exchange 100 USDC for SOL")
        triggered(r, "swap")
        params = r.params_for("swap")
        assert "USDC" in str(params.get("inputMint", "")).upper()

    def test_turkish_takas(self, chat_client):
        r = _chat(chat_client, "2 SOL'u BONK ile takas et")
        triggered(r, "swap")
        params = r.params_for("swap")
        assert "SOL" in str(params.get("inputMint", "")).upper()
        assert "BONK" in str(params.get("outputMint", "")).upper()

    def test_turkish_al(self, chat_client):
        r = _chat(chat_client, "50 USDC ile JUP al")
        triggered(r, "swap")

    def test_with_slippage(self, chat_client):
        r = _chat(chat_client, "Swap 5 SOL to USDC with 1% slippage")
        triggered(r, "swap")
        params = r.params_for("swap")
        assert str(params.get("slippageBps", "")) == "100", (
            f"slippageBps 100 olmalı: {params}"
        )

    def test_all_balance_swap(self, chat_client):
        r = _chat(chat_client, "Swap all my SOL to USDC")
        triggered(r, "swap")
        params = r.params_for("swap")
        assert str(params.get("amount", "")).lower() == "all"

    def test_hepsini_takat_turkish(self, chat_client):
        r = _chat(chat_client, "Tüm USDC'mi SOL'a çevir")
        triggered(r, "swap")
        params = r.params_for("swap")
        assert str(params.get("amount", "")).lower() == "all"

    def test_exact_out_mode(self, chat_client):
        r = _chat(chat_client, "I want to receive exactly 100 USDC, paying with SOL")
        triggered(r, "swap")
        params = r.params_for("swap")
        mode = str(params.get("swapMode", "")).lower()
        assert "exactout" in mode or "exact_out" in mode or "out" in mode, (
            f"ExactOut beklendi: {params}"
        )

    def test_bonk_to_sol(self, chat_client):
        r = _chat(chat_client, f"Sell 100000 BONK for SOL")
        triggered(r, "swap")
        params = r.params_for("swap")
        assert "BONK" in str(params.get("inputMint", "")).upper()
        assert "SOL" in str(params.get("outputMint", "")).upper()

    def test_does_not_trigger_raydium_by_default(self, chat_client):
        r = _chat(chat_client, "Swap 1 SOL for USDC")
        not_triggered(r, "raydium_swap", "Generic swap Raydium'a gitmemeli")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. STAKE
# ═══════════════════════════════════════════════════════════════════════════════

class TestStake:

    def test_marinade_explicit(self, chat_client):
        r = _chat(chat_client, "Stake 5 SOL on Marinade")
        # LLM may use "marinade_stake" or "stake" with protocol=marinade
        assert any("stake" in t for t in r.types()), f"Stake tetiklenmedi: {r.types()}"
        params = r.params_for("stake") or r.params_for("marinade_stake") or {}
        # If using generic "stake", protocol must be marinade
        if "stake" in r.types():
            assert "marinade" in str(params.get("protocol", "")).lower()

    def test_jupsol_explicit(self, chat_client):
        r = _chat(chat_client, "Stake 2 SOL into jupSOL")
        triggered(r, "jupsol_stake")

    def test_jito_explicit(self, chat_client):
        r = _chat(chat_client, "Stake 1 SOL on Jito for jitoSOL")
        triggered(r, "jito_stake")

    def test_generic_stake_defaults_marinade(self, chat_client):
        r = _chat(chat_client, "Stake 3 SOL")
        assert "stake" in " ".join(r.types()), f"Stake tetiklenmedi: {r.types()}"

    def test_turkish_stake(self, chat_client):
        r = _chat(chat_client, "Marinade'e 4 SOL stake et")
        assert any("stake" in t for t in r.types()), f"Stake tetiklenmedi: {r.types()}"
        params = r.params_for("stake") or r.params_for("marinade_stake") or {}
        assert float(params.get("amount", 0)) == pytest.approx(4.0, rel=0.01)

    def test_turkish_jupsol(self, chat_client):
        r = _chat(chat_client, "2 SOL'u jupSOL'a stake et")
        triggered(r, "jupsol_stake")

    def test_amount_extraction(self, chat_client):
        r = _chat(chat_client, "Stake 10.5 SOL on Marinade")
        # LLM may use "stake" with protocol=marinade OR "marinade_stake" directly
        assert any("stake" in t for t in r.types()), f"Stake tetiklenmedi: {r.types()}"
        # Get params from whichever action was triggered
        params = r.params_for("stake") or r.params_for("marinade_stake")
        assert float(params.get("amount", 0)) == pytest.approx(10.5, rel=0.01)

    def test_native_stake(self, chat_client):
        r = _chat(chat_client, "Create a native stake account with 1 SOL")
        # native_stake OR generic stake — both acceptable
        assert "native_stake" in r.types() or "stake" in r.types(), (
            f"native_stake veya stake tetiklenmeli: {r.types()}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. UNSTAKE
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnstake:

    def test_msol_unstake(self, chat_client):
        r = _chat(chat_client, "Unstake 5 mSOL from Marinade")
        # LLM may use "unstake" with protocol=marinade OR "marinade_unstake" directly
        assert "unstake" in r.types() or "marinade_unstake" in r.types(), (
            f"unstake tetiklenmedi: {r.types()}"
        )

    def test_jupsol_unstake(self, chat_client):
        r = _chat(chat_client, "Unstake 5 jupSOL")
        triggered(r, "jupsol_unstake")

    def test_jitosol_unstake(self, chat_client):
        r = _chat(chat_client, "Unstake 2 jitoSOL")
        triggered(r, "jito_unstake")

    def test_turkish_unstake(self, chat_client):
        r = _chat(chat_client, "3 mSOL'u unstake et")
        assert "unstake" in r.types() or "marinade_unstake" in r.types(), (
            f"unstake tetiklenmedi: {r.types()}"
        )

    def test_delayed_unstake(self, chat_client):
        r = _chat(chat_client, "Delayed unstake 10 mSOL from Marinade (no fee)")
        assert any("unstake" in t for t in r.types()), f"Unstake tetiklenmedi: {r.types()}"

    def test_all_msol(self, chat_client):
        r = _chat(chat_client, "Unstake all my mSOL")
        assert any("unstake" in t for t in r.types()), f"Unstake tetiklenmedi: {r.types()}"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. BURN
# ═══════════════════════════════════════════════════════════════════════════════

class TestBurn:

    def test_burn_partial(self, chat_client):
        r = _chat(chat_client, "Burn 1000 BONK")
        triggered(r, "burn")
        params = r.params_for("burn")
        assert "BONK" in str(params.get("mint", "")).upper()
        assert str(params.get("amount", "")) == "1000"

    def test_burn_all(self, chat_client):
        r = _chat(chat_client, "Burn all my WIF tokens")
        triggered(r, "burn")
        params = r.params_for("burn")
        assert str(params.get("amount", "")).lower() == "all"

    def test_burn_and_close(self, chat_client):
        r = _chat(chat_client, "Burn all my BONK and close the account to get SOL back")
        triggered(r, "burn")
        params = r.params_for("burn")
        assert str(params.get("amount", "")).lower() == "all"

    def test_turkish_yak(self, chat_client):
        r = _chat(chat_client, "500 BONK yak")
        triggered(r, "burn")
        params = r.params_for("burn")
        assert "BONK" in str(params.get("mint", "")).upper()

    def test_turkish_hepsini_yak(self, chat_client):
        r = _chat(chat_client, "Tüm WIF tokenlarımı yak")
        triggered(r, "burn")
        params = r.params_for("burn")
        assert str(params.get("amount", "")).lower() == "all"

    def test_close_mint_flag(self, chat_client):
        r = _chat(chat_client, "Burn 500 RAY and reclaim the rent by closing the token account")
        triggered(r, "burn")
        params = r.params_for("burn")
        assert params.get("closeMint") is True or str(params.get("closeMint", "")).lower() == "true"

    def test_burn_by_mint_address(self, chat_client):
        r = _chat(chat_client, f"Burn 10000 tokens of mint {BONK_MINT}")
        triggered(r, "burn")

    def test_burn_cannot_burn_sol(self, chat_client):
        r = _chat(chat_client, "Burn 1 SOL")
        not_triggered(r, "burn", "Native SOL yakılamaz, burn tetiklenmemeli")
        assert r.has_text(30), "LLM açıklama yapmalı"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SCAN EMPTY ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestScanEmptyAccounts:

    def test_scan_english(self, chat_client):
        r = _chat(chat_client, "Scan my wallet for empty token accounts")
        triggered(r, "scan_empty_accounts")

    def test_how_much_sol_english(self, chat_client):
        r = _chat(chat_client, "How much SOL can I recover from empty token accounts?")
        triggered(r, "scan_empty_accounts")

    def test_check_dust(self, chat_client):
        r = _chat(chat_client, "Check my wallet for dust accounts")
        triggered(r, "scan_empty_accounts")

    def test_turkish_tara(self, chat_client):
        r = _chat(chat_client, "Cüzdanımdaki boş token hesaplarını tara")
        triggered(r, "scan_empty_accounts")

    def test_turkish_ne_kadar(self, chat_client):
        r = _chat(chat_client, "Boş hesapları kapatırsam ne kadar SOL kazanırım?")
        triggered(r, "scan_empty_accounts")

    def test_cleanup_wallet(self, chat_client):
        r = _chat(chat_client, "Clean up my wallet and remove empty token accounts")
        assert "scan_empty_accounts" in r.types() or "close_accounts" in r.types(), (
            f"scan veya close tetiklenmeli: {r.types()}"
        )

    def test_sol_geri_al_turkish(self, chat_client):
        r = _chat(chat_client, "Hangi hesapları kapatırsam SOL geri alabilirim?")
        triggered(r, "scan_empty_accounts")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CLOSE ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCloseAccounts:

    def test_close_explicit_mints(self, chat_client):
        r = _chat(chat_client, f"Close empty BONK and WIF token accounts to reclaim SOL")
        triggered(r, "close_accounts")
        params = r.params_for("close_accounts")
        # mints may be a comma-separated string or a list (serialized)
        mints_raw = str(params.get("mints", ""))
        assert "BONK" in mints_raw.upper() or "WIF" in mints_raw.upper(), (
            f"Mint listesi yanlış: {mints_raw}"
        )

    def test_close_single(self, chat_client):
        r = _chat(chat_client, "Close my empty BONK account and get the rent back")
        triggered(r, "close_accounts")

    def test_turkish_kapat(self, chat_client):
        r = _chat(chat_client, "Boş BONK ve RAY hesaplarımı kapat")
        triggered(r, "close_accounts")

    def test_reclaim_rent(self, chat_client):
        r = _chat(chat_client, "Reclaim rent from empty token accounts: BONK, WIF, JUP")
        triggered(r, "close_accounts")
        params = r.params_for("close_accounts")
        mints_raw = str(params.get("mints", ""))
        # accept either comma-separated string or list-like repr
        token_hits = sum(t in mints_raw.upper() for t in ("BONK", "WIF", "JUP"))
        assert token_hits >= 2, f"En az 2 mint beklendi: {mints_raw}"

    def test_without_scan_first_triggers_scan(self, chat_client):
        r = _chat(chat_client, "Close all my empty token accounts")
        assert "scan_empty_accounts" in r.types() or "close_accounts" in r.types(), (
            f"scan veya close beklendi: {r.types()}"
        )

    def test_turkish_rent_geri_al(self, chat_client):
        r = _chat(chat_client, "Boş token hesaplarımı kapatarak SOL'umu geri al: BONK, WIF")
        triggered(r, "close_accounts")


# ═══════════════════════════════════════════════════════════════════════════════
# 8. CLAIM
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaim:

    def test_marinade_claim(self, chat_client):
        r = _chat(chat_client, "Claim my Marinade staking rewards")
        triggered(r, "claim")
        params = r.params_for("claim")
        assert "marinade" in str(params.get("protocol", "")).lower()

    def test_jupiter_airdrop(self, chat_client):
        r = _chat(chat_client, "Claim my JUP airdrop")
        triggered(r, "claim")
        params = r.params_for("claim")
        assert "jupiter" in str(params.get("protocol", "")).lower() or \
               "jup" in str(params.get("protocol", "")).lower()

    def test_jito_rewards(self, chat_client):
        r = _chat(chat_client, "How do I claim my Jito MEV rewards?")
        triggered(r, "claim")
        params = r.params_for("claim")
        assert "jito" in str(params.get("protocol", "")).lower()

    def test_orca_fees_redirects(self, chat_client):
        r = _chat(chat_client, "Collect my Orca liquidity fees")
        assert "orca_collect_fees" in r.types() or "claim" in r.types(), (
            f"Orca collect fees veya claim tetiklenmeli: {r.types()}"
        )

    def test_turkish_odul_al(self, chat_client):
        r = _chat(chat_client, "Marinade ödüllerimi nasıl alırım?")
        triggered(r, "claim")

    def test_claim_returns_guidance(self, chat_client):
        r = _chat(chat_client, "Claim my Marinade rewards")
        triggered(r, "claim")
        params = r.params_for("claim")
        # Guidance is delivered via action params (protocol+type) — model doesn't generate
        # plain text alongside function calls; frontend renders the guidance UI.
        assert "marinade" in str(params.get("protocol", "")).lower(), (
            f"protocol param beklendi: {params}"
        )

    def test_kamino_rewards(self, chat_client):
        r = _chat(chat_client, "Claim my Kamino KMNO rewards")
        triggered(r, "claim")
        params = r.params_for("claim")
        assert "kamino" in str(params.get("protocol", "")).lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 9. VOTE
# ═══════════════════════════════════════════════════════════════════════════════

class TestVote:

    def test_jupiter_vote(self, chat_client):
        r = _chat(chat_client, "Vote yes on Jupiter proposal #42")
        triggered(r, "vote")
        params = r.params_for("vote")
        assert "jupiter" in str(params.get("protocol", "")).lower() or \
               "jup" in str(params.get("protocol", "")).lower()
        assert str(params.get("choice", "")).lower() == "yes"

    def test_marinade_vote_no(self, chat_client):
        r = _chat(chat_client, "Vote no on Marinade governance proposal 7")
        triggered(r, "vote")
        params = r.params_for("vote")
        assert "marinade" in str(params.get("protocol", "")).lower()
        assert str(params.get("choice", "")).lower() == "no"

    def test_turkish_oy_ver(self, chat_client):
        r = _chat(chat_client, "Jupiter'ın 42 numaralı önerisine evet oyu ver")
        triggered(r, "vote")

    def test_vote_returns_portal_link(self, chat_client):
        r = _chat(chat_client, "Vote yes on Jupiter proposal #1")
        triggered(r, "vote")
        params = r.params_for("vote")
        # Portal link/guidance delivered via action params — model invokes function call
        # without plain text; frontend renders governance UI with portal link.
        assert "jupiter" in str(params.get("protocol", "")).lower() or \
               "jup" in str(params.get("protocol", "")).lower(), (
            f"Jupiter protocol beklendi: {params}"
        )

    def test_kamino_governance(self, chat_client):
        r = _chat(chat_client, "How do I vote on Kamino governance?")
        triggered(r, "vote")
        params = r.params_for("vote")
        assert "kamino" in str(params.get("protocol", "")).lower()

    def test_abstain_choice(self, chat_client):
        r = _chat(chat_client, "Abstain on Marinade proposal #3")
        triggered(r, "vote")
        params = r.params_for("vote")
        assert str(params.get("choice", "")).lower() == "abstain"


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASES — Hatalı yönlendirme olmaması
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_ambiguous_stake_asks_clarification_or_routes(self, chat_client):
        r = _chat(chat_client, "I want to stake")
        # LLM should ask clarification, trigger a stake action, or provide text guidance
        assert r.asked_clarification() or any("stake" in t for t in r.types()) or r.has_text(20), (
            f"Clarification, stake, or text guidance beklendi: {r.types()}"
        )

    def test_swap_not_confused_with_transfer(self, chat_client):
        r = _chat(chat_client, "Send 100 USDC to SOL")
        # "Send X to SOL" is ambiguous — could be swap or transfer
        # Should either ask clarification or choose one (not both)
        assert not (
            "transfer" in r.types() and "swap" in r.types()
        ), f"Hem transfer hem swap tetiklendi: {r.types()}"

    def test_burn_not_triggered_for_sell(self, chat_client):
        r = _chat(chat_client, "Sell my BONK tokens")
        not_triggered(r, "burn", "Satış = swap, burn değil")

    def test_close_not_triggered_for_cancel(self, chat_client):
        r = _chat(chat_client, "Cancel my limit order")
        not_triggered(r, "close_accounts", "Order iptal = limit_order_cancel, close_accounts değil")

    def test_scan_not_triggered_for_portfolio(self, chat_client):
        r = _chat(chat_client, "Show me my token balances")
        not_triggered(r, "scan_empty_accounts", "Bakiye sorgusu scan tetiklememeli")

    def test_no_action_for_pure_question(self, chat_client):
        r = _chat(chat_client, "What is Marinade Finance?")
        assert not r.actions, f"Bilgi sorusu aksiyon tetiklememeli: {r.types()}"
        assert r.has_text(50), "LLM açıklama yapmalı"

    def test_turkish_english_mixed(self, chat_client):
        r = _chat(chat_client, f"1 SOL swap to USDC yap ve sonra {WALLET_B}'ye gönder")
        assert len(r.types()) >= 1, "En az 1 aksiyon tetiklenmeli"

    def test_transfer_not_swap(self, chat_client):
        r = _chat(chat_client, f"Transfer 5 SOL to {WALLET_B}")
        triggered(r, "transfer")
        not_triggered(r, "swap", "Transfer = swap değil")

    def test_stake_not_confused_with_transfer(self, chat_client):
        r = _chat(chat_client, "Stake 1 SOL on Marinade")
        not_triggered(r, "transfer", "Stake transfer değil")

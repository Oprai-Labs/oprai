"""
MarginFi LLM End-to-End Test Suite
────────────────────────────────────
Tüm 8 MarginFi tool'unu tüm parametre kombinasyonları ve uç case'lerle
LLM üzerinden test eder. Hem tool seçimi hem HTML yanıt kalitesi ölçülür.

Çalıştır:
  cd services/defi-query-service
  python3 tests/e2e_llm_marginfi.py
  python3 tests/e2e_llm_marginfi.py --tool marginfi_lst_rates
  python3 tests/e2e_llm_marginfi.py --id lst_01_best_apy
  python3 tests/e2e_llm_marginfi.py --fast
"""

import asyncio
import re
import sys
import time
import os
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── Bilinen adresler ────────────────────────────────────────────────────────

EMPTY_WALLET = "GJGkJEFPDdFwPsnCyJqoYJGZTHyv9NM6qwqJvLSHFCH"
FAKE_WALLET  = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAKE_BANK    = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

# Bilinen MarginFi bank adresleri (mrgn-bank-metadata-cache'den)
SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ─── Yardımcı fonksiyonlar ───────────────────────────────────────────────────

def html_has(html: str, *keywords: str) -> dict[str, bool]:
    plain = re.sub(r"<[^>]+>", "", html).lower()
    return {kw: kw.lower() in plain for kw in keywords}

def check_has_percentage(html: str) -> bool:
    return bool(re.search(r"[\d.]+\s*%", html))

def check_html_structure(html: str) -> dict:
    return {
        "has_wrapper":      "font-family" in html or "background" in html,
        "has_insight_box":  "💡" in html or "insight" in html.lower(),
        "has_warning_box":  "⚠" in html or "warning" in html.lower(),
        "has_badges":       "border-radius:12px" in html or "border-radius: 12px" in html,
        "has_percentage":   check_has_percentage(html),
        "html_length":      len(html),
    }

def analyze_interpretation(html: str, expected_fields: list[str]) -> dict:
    plain = re.sub(r"<[^>]+>", "", html).lower()
    found = {f: f.lower() in plain for f in expected_fields}
    coverage = sum(found.values()) / len(found) if found else 0
    return {"field_coverage": coverage, "found_fields": found}

# ─── Test yapısı ─────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    id: str
    tool: str
    query: str
    expected_tools: list[str]
    check_html: list[str] = field(default_factory=list)
    check_fields: list[str] = field(default_factory=list)
    must_warn: bool = False
    description: str = ""

@dataclass
class TestResult:
    case: TestCase
    tools_called: list[str]
    html: str
    plain: str
    duration: float
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return any(t in self.tools_called for t in self.case.expected_tools)

    @property
    def tool_ok(self) -> bool:
        return any(t in self.tools_called for t in self.case.expected_tools)

    def html_checks(self) -> dict:
        return html_has(self.html, *self.case.check_html) if self.case.check_html else {}

    def field_analysis(self) -> dict:
        return analyze_interpretation(self.html, self.case.check_fields) if self.case.check_fields else {}

    def structure(self) -> dict:
        return check_html_structure(self.html)


# ─── Test case'leri ──────────────────────────────────────────────────────────

ALL_CASES: list[TestCase] = [

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_BANKS — 97 bank, bankAddress + tokenAddress + tokenSymbol
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="banks_01_list",
        tool="marginfi_banks",
        query="What tokens can I borrow or lend on MarginFi?",
        expected_tools=["marginfi_banks"],
        check_html=["marginfi", "token", "bank"],
        check_fields=["bankAddress", "tokenSymbol"],
        description="Desteklenen asset listesi",
    ),
    TestCase(
        id="banks_02_usdc_check",
        tool="marginfi_banks",
        query="Does MarginFi support USDC? What is the bank address for USDC on MarginFi?",
        expected_tools=["marginfi_banks"],
        check_html=["usdc", "marginfi", "bank"],
        check_fields=["bankAddress", "tokenAddress"],
        description="USDC bank adresi sorgusu",
    ),
    TestCase(
        id="banks_03_sol_check",
        tool="marginfi_banks",
        query="Is SOL available for lending on MarginFi? Show me the SOL bank details.",
        expected_tools=["marginfi_banks"],
        check_html=["sol", "marginfi", "bank"],
        check_fields=["bankAddress", "tokenSymbol"],
        description="SOL bank sorgulama",
    ),
    TestCase(
        id="banks_04_count",
        tool="marginfi_banks",
        query="How many assets/banks does MarginFi support in total?",
        expected_tools=["marginfi_banks"],
        check_html=["marginfi", "bank"],
        check_fields=["tokenSymbol"],
        description="Toplam bank sayısı",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_STAKED_BANKS — 51 staked bank + validatorVoteAccount
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="stk_banks_01_list",
        tool="marginfi_staked_banks",
        query="Which validators are available for staking on MarginFi?",
        expected_tools=["marginfi_staked_banks"],
        check_html=["validator", "marginfi", "staking"],
        check_fields=["validatorVoteAccount", "tokenSymbol"],
        description="Validator listesi",
    ),
    TestCase(
        id="stk_banks_02_count",
        tool="marginfi_staked_banks",
        query="How many staked banks does MarginFi have? Show me the staked asset list.",
        expected_tools=["marginfi_staked_banks"],
        check_html=["staked", "bank", "marginfi"],
        check_fields=["bankAddress", "validatorVoteAccount"],
        description="Staked bank sayısı ve listesi",
    ),
    TestCase(
        id="stk_banks_03_validator_vote",
        tool="marginfi_staked_banks",
        query="Show me MarginFi staked banks with their validator vote accounts.",
        expected_tools=["marginfi_staked_banks"],
        check_html=["validator", "vote", "marginfi"],
        check_fields=["validatorVoteAccount", "bankAddress"],
        description="Validator vote account sorgusu",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_STAKED_TOKEN_METADATA — 51 staked token (symbol, address, name, decimals)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="stk_meta_01_list",
        tool="marginfi_staked_token_metadata",
        query="List all staked tokens supported by MarginFi with their addresses.",
        expected_tools=["marginfi_staked_token_metadata"],
        check_html=["token", "address", "marginfi"],
        check_fields=["symbol", "address"],
        description="Staked token listesi",
    ),
    TestCase(
        id="stk_meta_02_decimals",
        tool="marginfi_staked_token_metadata",
        query="What are the decimal places for MarginFi staked tokens?",
        expected_tools=["marginfi_staked_token_metadata"],
        check_html=["token", "decimals", "marginfi"],
        check_fields=["symbol", "decimals"],
        description="Staked token decimals sorgusu",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_TOKEN_METADATA — 82 tokens (symbol, address, logoURI, coingeckoId, socials)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="meta_01_list",
        tool="marginfi_token_metadata",
        query="Show me all tokens supported by MarginFi with their CoinGecko IDs.",
        expected_tools=["marginfi_token_metadata"],
        check_html=["token", "marginfi", "coingecko"],
        check_fields=["symbol", "address"],
        description="Token metadata listesi + coingecko",
    ),
    TestCase(
        id="meta_02_sol_details",
        tool="marginfi_token_metadata",
        query="What is the MarginFi token metadata for SOL? Show logo and external links.",
        expected_tools=["marginfi_token_metadata"],
        check_html=["sol", "marginfi", "token"],
        check_fields=["symbol", "address"],
        description="SOL token metadata",
    ),
    TestCase(
        id="meta_03_full_list",
        tool="marginfi_token_metadata",
        query="Give me the full MarginFi supported token list with all details.",
        expected_tools=["marginfi_token_metadata"],
        check_html=["token", "marginfi"],
        check_fields=["symbol", "address", "decimals"],
        description="Tam token listesi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_LST_RATES — 23 LST, symbol + mint + lst_apy
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="lst_01_best_apy",
        tool="marginfi_lst_rates",
        query="Which LST token gives the highest staking APY on MarginFi?",
        expected_tools=["marginfi_lst_rates"],
        check_html=["apy", "lst", "marginfi", "%"],
        check_fields=["symbol", "lst_apy"],
        description="En yüksek LST APY sıralaması",
    ),
    TestCase(
        id="lst_02_compare_dsol_bsol",
        tool="marginfi_lst_rates",
        query="Compare dSOL and bSOL staking APY on MarginFi. Which is better?",
        expected_tools=["marginfi_lst_rates"],
        check_html=["dsol", "bsol", "apy", "%"],
        check_fields=["symbol", "lst_apy"],
        description="dSOL vs bSOL APY karşılaştırması",
    ),
    TestCase(
        id="lst_03_full_ranking",
        tool="marginfi_lst_rates",
        query="Show all liquid staking token APY rates on MarginFi, ranked from highest to lowest.",
        expected_tools=["marginfi_lst_rates"],
        check_html=["apy", "marginfi", "%"],
        check_fields=["symbol", "lst_apy", "mint"],
        description="Tüm LST APY sıralaması",
    ),
    TestCase(
        id="lst_04_vs_native_stake",
        tool="marginfi_lst_rates",
        query="Is MarginFi LST staking better than native Solana staking? What APY can I get?",
        expected_tools=["marginfi_lst_rates"],
        check_html=["apy", "staking", "marginfi", "%"],
        check_fields=["symbol", "lst_apy"],
        description="LST vs native stake karşılaştırması",
    ),
    TestCase(
        id="lst_05_yield_strategy",
        tool="marginfi_lst_rates",
        query="I have 10 SOL. What is the best LST strategy on MarginFi to maximize yield?",
        expected_tools=["marginfi_lst_rates"],
        check_html=["sol", "apy", "marginfi", "%"],
        check_fields=["symbol", "lst_apy"],
        description="SOL için en iyi LST strateji",
    ),
    TestCase(
        id="lst_06_dsol_specific",
        tool="marginfi_lst_rates",
        query="What is the current APY for dSOL on MarginFi?",
        expected_tools=["marginfi_lst_rates"],
        check_html=["dsol", "apy", "%"],
        check_fields=["symbol", "lst_apy"],
        description="dSOL spesifik APY sorgusu",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_PRIORITY_FEES — min/max prioritizationFee (microlamports)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="fee_01_current",
        tool="marginfi_priority_fees",
        query="What are the current recommended priority fees for MarginFi transactions?",
        expected_tools=["marginfi_priority_fees"],
        check_html=["fee", "marginfi"],
        check_fields=["min", "max"],
        description="Güncel priority fee sorgusu",
    ),
    TestCase(
        id="fee_02_how_much_to_set",
        tool="marginfi_priority_fees",
        query="How much priority fee should I set for a MarginFi deposit transaction right now?",
        expected_tools=["marginfi_priority_fees"],
        check_html=["fee", "marginfi", "transaction"],
        check_fields=["min", "max"],
        description="İşlem için tavsiye edilen fee",
    ),
    TestCase(
        id="fee_03_network_congestion",
        tool="marginfi_priority_fees",
        query="Is the Solana network congested right now? What are MarginFi's priority fees showing?",
        expected_tools=["marginfi_priority_fees"],
        check_html=["fee", "network"],
        check_fields=["min", "max"],
        description="Ağ yoğunluğu + fee analizi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_BANK_HISTORIC — requires bank_address (multi-step flow)
    # LLM önce marginfi_banks'ten adres almalı, sonra historic çağırmalı
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="hist_01_usdc_historic",
        tool="marginfi_bank_historic",
        query="Show me the historical lending APY for USDC on MarginFi. How has the rate changed over time?",
        expected_tools=["marginfi_bank_historic", "marginfi_banks"],
        check_html=["usdc", "apy", "marginfi"],
        check_fields=["apy"],
        description="USDC bank geçmiş APY (multi-step)",
    ),
    TestCase(
        id="hist_02_sol_historic",
        tool="marginfi_bank_historic",
        query="What does the historical SOL lending rate look like on MarginFi? Is it trending up or down?",
        expected_tools=["marginfi_bank_historic", "marginfi_banks"],
        check_html=["sol", "marginfi"],
        check_fields=["apy"],
        description="SOL bank geçmiş APY trend (multi-step)",
    ),
    TestCase(
        id="hist_03_invalid_bank",
        tool="marginfi_bank_historic",
        query=f"Show me historical data for MarginFi bank at address: {FAKE_BANK}",
        expected_tools=["marginfi_bank_historic"],
        check_html=["marginfi"],
        must_warn=True,
        description="Geçersiz bank adresi — hata yönetimi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARGINFI_VALIDATOR_INFO — requires validator pubkey (multi-step)
    # LLM önce marginfi_staked_banks'ten vote account almalı
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="val_01_first_validator",
        tool="marginfi_validator_info",
        query="Show me details about the first validator available on MarginFi staking — commission, APY, and stake.",
        expected_tools=["marginfi_validator_info", "marginfi_staked_banks"],
        check_html=["validator", "marginfi"],
        check_fields=["commission", "apy"],
        description="İlk validator detayı (multi-step)",
    ),
    TestCase(
        id="val_02_best_validator",
        tool="marginfi_validator_info",
        query="Which validator on MarginFi has the lowest commission? Look up staked banks first then get validator details.",
        expected_tools=["marginfi_staked_banks", "marginfi_validator_info"],
        check_html=["validator", "commission", "marginfi"],
        check_fields=["commission"],
        description="En düşük komisyonlu validator (multi-step)",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # EDGE CASES — multi-tool zincirleme, karşılaştırmalar, protokol genel bakış
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="edge_01_marginfi_vs_kamino_lending",
        tool="marginfi_banks",
        query="Should I use MarginFi or Kamino for USDC lending? Which gives better rates?",
        expected_tools=["marginfi_banks", "kamino_market_reserves"],
        check_html=["marginfi", "kamino", "usdc"],
        check_fields=["apy", "tokenSymbol"],
        description="MarginFi vs Kamino USDC lending karşılaştırması",
    ),
    TestCase(
        id="edge_02_lst_cross_protocol",
        tool="marginfi_lst_rates",
        query="Compare LST staking APY on MarginFi vs Kamino. Which protocol gives better returns for JitoSOL or mSOL?",
        expected_tools=["marginfi_lst_rates", "kamino_staking_yields"],
        check_html=["marginfi", "kamino", "apy", "%"],
        check_fields=["symbol", "lst_apy"],
        description="MarginFi vs Kamino LST APY çapraz protokol",
    ),
    TestCase(
        id="edge_03_marginfi_overview",
        tool="marginfi_banks",
        query="Give me a full overview of MarginFi — how many banks, which tokens are supported, and what LST rates are available.",
        expected_tools=["marginfi_banks", "marginfi_lst_rates"],
        check_html=["marginfi", "bank", "apy"],
        check_fields=["tokenSymbol", "lst_apy"],
        description="MarginFi protokol genel bakış",
    ),
    TestCase(
        id="edge_04_staking_strategy",
        tool="marginfi_lst_rates",
        query=(
            "I want to stake 5 SOL. Compare MarginFi LST options with Jito staking. "
            "Which gives the best APY and what are the risks?"
        ),
        expected_tools=["marginfi_lst_rates", "jito_stake_pool_stats"],
        check_html=["sol", "apy", "staking", "%"],
        check_fields=["symbol", "lst_apy"],
        description="SOL stake strateji — MarginFi + Jito karşılaştırması",
    ),
    TestCase(
        id="edge_05_complete_marginfi_data",
        tool="marginfi_banks",
        query=(
            "Show me everything about MarginFi: supported tokens, staked banks, "
            "LST APY rates, and current priority fees."
        ),
        expected_tools=["marginfi_banks", "marginfi_lst_rates", "marginfi_priority_fees"],
        check_html=["marginfi", "token", "apy", "fee"],
        check_fields=["tokenSymbol", "lst_apy"],
        description="Tam MarginFi veri çekme — 4 tool",
    ),
    TestCase(
        id="edge_06_error_handling_fake_wallet",
        tool="marginfi_banks",
        query=f"Show me MarginFi positions for wallet {FAKE_WALLET}",
        expected_tools=["marginfi_banks"],
        check_html=["marginfi"],
        must_warn=False,
        description="Geçersiz cüzdan — LLM'in uygun hata vermesi",
    ),
]


# ─── Test koşucusu ───────────────────────────────────────────────────────────

async def run_single(case: TestCase, query_fn) -> TestResult:
    t0 = time.time()
    for attempt in range(3):
        try:
            result = await query_fn(case.query)
            return TestResult(
                case=case,
                tools_called=result["tools_called"],
                html=result["html"],
                plain=result["plain"],
                duration=time.time() - t0,
            )
        except Exception as e:
            err = str(e)
            if "429" in err and attempt < 2:
                wait = 20 + attempt * 15
                print(f"   [429 rate limit — {wait}s bekleniyor, deneme {attempt+2}/3]")
                await asyncio.sleep(wait)
                continue
            return TestResult(
                case=case,
                tools_called=[],
                html="",
                plain="",
                duration=time.time() - t0,
                error=err,
            )


def print_result(r: TestResult, fast: bool = False):
    status = "✅" if r.passed else "❌"
    tool_ok = "✓" if r.tool_ok else "✗"
    print(f"\n{'─'*70}")
    print(f"{status} [{r.case.id}] {r.case.description}")
    print(f"   Query    : {r.case.query[:90]}{'...' if len(r.case.query)>90 else ''}")
    print(f"   Tools    : {r.tools_called}  {tool_ok}")
    print(f"   Süre     : {r.duration:.1f}s  |  HTML: {len(r.html)} karakter")

    if r.error:
        print(f"   HATA     : {r.error[:150]}")
        return

    if not fast:
        struct = r.structure()
        checks = []
        if struct["has_wrapper"]:      checks.append("🏗wrapper")
        if struct["has_insight_box"]:  checks.append("💡insight")
        if struct["has_warning_box"]:  checks.append("⚠warning")
        if struct["has_badges"]:       checks.append("🏷badges")
        if struct["has_percentage"]:   checks.append("📊%")
        print(f"   HTML     : {' | '.join(checks) or 'ZAYIF'}")

        if r.case.check_html:
            kw = r.html_checks()
            found = [k for k, v in kw.items() if v]
            miss  = [k for k, v in kw.items() if not v]
            if found: print(f"   ✓ Kelime : {found}")
            if miss:  print(f"   ✗ Eksik  : {miss}")

        if r.case.check_fields:
            fa = r.field_analysis()
            cov = fa["field_coverage"]
            bar = "█" * int(cov * 10) + "░" * (10 - int(cov * 10))
            miss_f = [f for f, ok in fa["found_fields"].items() if not ok]
            print(f"   Yorum    : [{bar}] {cov*100:.0f}%", end="")
            if miss_f: print(f"  ✗ {miss_f}")
            else: print()

        if r.case.must_warn:
            has_warn = r.structure()["has_warning_box"]
            print(f"   Hata yön : {'✅ VAR' if has_warn else '❌ EKSİK'}")

    plain_preview = re.sub(r"\s+", " ", r.plain[:220]).strip()
    print(f"   Yanıt    : {plain_preview}")


async def run_all(filter_val: Optional[str] = None, fast: bool = False):
    from orchestrator import query as _query

    cases = ALL_CASES
    if filter_val:
        cases = [c for c in cases if c.tool == filter_val or c.id == filter_val]

    print(f"\n{'═'*70}")
    print(f"MarginFi LLM End-to-End Test Suite")
    print(f"{'═'*70}")
    print(f"Toplam test: {len(cases)} | Mod: {'HIZLI' if fast else 'DETAYLI'}")
    if filter_val:
        print(f"Filtre: {filter_val}")

    results: list[TestResult] = []

    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {case.id} çalıştırılıyor...", flush=True)
        r = await run_single(case, _query)
        results.append(r)
        print_result(r, fast)
        if i < len(cases):
            await asyncio.sleep(2)

    total   = len(results)
    passed  = sum(1 for r in results if r.passed)
    tool_ok = sum(1 for r in results if r.tool_ok)
    errors  = sum(1 for r in results if r.error)
    avg_dur = sum(r.duration for r in results) / total if total else 0
    avg_html = sum(len(r.html) for r in results) / total if total else 0

    cov_vals = [r.field_analysis()["field_coverage"] for r in results if r.case.check_fields and not r.error]
    avg_cov  = sum(cov_vals) / len(cov_vals) if cov_vals else 0

    print(f"\n{'═'*70}")
    print(f"GENEL SONUÇ")
    print(f"{'═'*70}")
    print(f"  Toplam test   : {total}")
    print(f"  ✅ Başarılı   : {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"  🎯 Doğru tool : {tool_ok}/{total} ({tool_ok/total*100:.0f}%)")
    print(f"  ❌ Hata       : {errors}")
    print(f"  ⏱ Ort süre   : {avg_dur:.1f}s/test")
    print(f"  📄 Ort HTML   : {avg_html:.0f} karakter")
    print(f"  📊 Yorum kap. : {avg_cov*100:.0f}%")

    print(f"\n{'─'*70}")
    print("BAŞARISIZ TESTLER:")
    failed = [r for r in results if not r.passed]
    if not failed:
        print("  Hepsi geçti! 🎉")
    else:
        for r in failed:
            reason = r.error or f"Beklenen: {r.case.expected_tools}, Gelen: {r.tools_called}"
            print(f"  ✗ {r.case.id}: {reason[:130]}")

    print(f"\n{'─'*70}")
    print("TOOL BAZLI ÖZET:")
    from collections import defaultdict
    by_tool: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_tool[r.case.tool].append(r)
    for tool, tool_results in sorted(by_tool.items()):
        ok = sum(1 for r in tool_results if r.passed)
        print(f"  {tool:40s}: {ok}/{len(tool_results)} ✅")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", help="Sadece belirli bir tool (ör: marginfi_lst_rates)")
    parser.add_argument("--id",   help="Belirli bir test ID'si (ör: lst_01_best_apy)")
    parser.add_argument("--fast", action="store_true", help="Kısa çıktı modu")
    args = parser.parse_args()

    asyncio.run(run_all(args.id or args.tool, args.fast))

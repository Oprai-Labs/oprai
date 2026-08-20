"""
Marinade LLM extended suite — many phrasings, plus HTML quality analysis
─────────────────────────────────────────────────────────────────────────────
Informal, advice-seeking, comparative, Turkish, numeric, vague and multi-step
edge cases — scored against the real rendered HTML.

The Turkish questions are deliberate: they are the regression net for
Turkish-language intent handling.

Run:
  cd services/defi-query-service
  python3 tests/e2e_llm_marinade_extended.py
  python3 tests/e2e_llm_marinade_extended.py --id informal_01
  python3 tests/e2e_llm_marinade_extended.py --group informal
  python3 tests/e2e_llm_marinade_extended.py --print-html   # print the full HTML
"""

import asyncio
import re
import sys
import os
import time
import argparse
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── HTML quality scoring ────────────────────────────────────────────────────

def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)

def html_has(html: str, *keywords: str) -> dict[str, bool]:
    plain = strip_tags(html).lower()
    return {kw: kw.lower() in plain for kw in keywords}

def check_has_percentage(html: str) -> bool:
    return bool(re.search(r"[\d.]+\s*%", html))

def check_has_number(html: str) -> bool:
    return bool(re.search(r"\b\d+[\d,.]*\b", strip_tags(html)))

def html_quality_score(html: str) -> dict:
    """A 0-100 quality score, plus the breakdown."""
    score = 0
    details = {}

    details["has_wrapper"] = "font-family" in html or 'style="' in html
    if details["has_wrapper"]: score += 15

    details["has_insight_box"] = "💡" in html or "insight" in html.lower()
    if details["has_insight_box"]: score += 15

    details["has_warning_box"] = "⚠" in html or "warning" in html.lower() or "caution" in html.lower()
    if details["has_warning_box"]: score += 5

    details["has_table"] = "<table" in html
    if details["has_table"]: score += 10

    details["has_stat_cards"] = ("border-radius" in html and "padding" in html)
    if details["has_stat_cards"]: score += 10

    details["has_percentage"] = check_has_percentage(html)
    if details["has_percentage"]: score += 15

    details["has_number"] = check_has_number(html)
    if details["has_number"]: score += 10

    details["length_ok"] = len(html) > 800
    if details["length_ok"]: score += 10

    details["not_raw_json"] = "{" not in strip_tags(html)[:200]
    if details["not_raw_json"]: score += 10

    details["total_score"] = score
    return details


# ─── Test scaffolding ────────────────────────────────────────────────────────

@dataclass
class TestCase:
    id: str
    group: str
    query: str
    expected_tools: list[str]
    check_html: list[str] = field(default_factory=list)
    min_quality: int = 50
    must_have_percentage: bool = False
    must_have_table: bool = False
    must_have_insight: bool = False
    description: str = ""

@dataclass
class TestResult:
    case: TestCase
    tools_called: list[str]
    html: str
    plain: str
    duration: float
    quality: dict
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return any(t in self.tools_called for t in self.case.expected_tools)

    @property
    def quality_score(self) -> int:
        return self.quality.get("total_score", 0)

    def kw_checks(self) -> dict:
        return html_has(self.html, *self.case.check_html) if self.case.check_html else {}


# ─── Test case'leri ──────────────────────────────────────────────────────────

ALL_CASES: list[TestCase] = [

    # ═══════════════════════════════════════════════════════════════════
    # INFORMAL — how ordinary users ask
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="informal_01_worth_it",
        group="informal",
        query="Is mSOL staking actually worth it? What's the APY?",
        expected_tools=["marinade_msol_apy", "marinade_stats"],
        check_html=["apy", "msol", "%"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Informal: mSOL worth it?",
    ),
    TestCase(
        id="informal_02_whats_marinade",
        group="informal",
        query="What even is Marinade Finance? Give me a quick summary of what they do and their key stats.",
        expected_tools=["marinade_stats"],
        check_html=["marinade", "msol", "tvl"],
        must_have_insight=True,
        description="Informal: what is Marinade?",
    ),
    TestCase(
        id="informal_03_should_stake",
        group="informal",
        query="Should I stake my SOL with Marinade? What are the pros and cons?",
        expected_tools=["marinade_stats", "marinade_msol_apy", "marinade_native_apy"],
        check_html=["marinade", "apy", "sol"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Informal: should I stake with Marinade?",
    ),
    TestCase(
        id="informal_04_safe",
        group="informal",
        query="Is mSOL safe? What validators does Marinade stake to and how are they selected?",
        expected_tools=["marinade_validators", "marinade_config"],
        check_html=["validator", "marinade", "score"],
        must_have_insight=True,
        description="Informal: is mSOL safe?",
    ),
    TestCase(
        id="informal_05_quick_apy",
        group="informal",
        query="Hey quick question — what's mSOL APY right now?",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        must_have_percentage=True,
        description="Informal: quick APY question",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # INVESTMENT — advice-seeking questions
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="invest_01_10_sol",
        group="investment",
        query="I have 10 SOL. If I stake it with Marinade for 1 year, approximately how much would I earn?",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "sol", "msol", "%"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Investment: 10 SOL for 1 year — earnings estimate",
    ),
    TestCase(
        id="invest_02_100_sol",
        group="investment",
        query="If I stake 100 SOL with Marinade native staking vs mSOL liquid staking, what's the difference in annual returns?",
        expected_tools=["marinade_native_apy", "marinade_msol_apy"],
        check_html=["apy", "sol", "marinade", "%"],
        must_have_percentage=True,
        must_have_table=True,
        description="Investment: 100 SOL — native vs liquid comparison",
    ),
    TestCase(
        id="invest_03_best_option",
        group="investment",
        query="I want maximum SOL yield with minimum risk. Should I use mSOL, native Marinade staking, or jitoSOL?",
        expected_tools=["marinade_msol_apy", "marinade_native_apy", "jito_stake_pool_stats"],
        check_html=["apy", "sol", "marinade", "%"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Investment: max yield min risk — protocol comparison",
    ),
    TestCase(
        id="invest_04_long_term",
        group="investment",
        query="Long-term staking: what's the 2-year APY for mSOL vs 1-year? Is holding mSOL long-term beneficial?",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "2", "1", "%"],
        must_have_percentage=True,
        must_have_table=True,
        description="Investment: long-term 2y vs 1y mSOL APY",
    ),
    TestCase(
        id="invest_05_risk_validator",
        group="investment",
        query="Which Marinade validators have raised their commission recently? I want to avoid them as a delegator.",
        expected_tools=["marinade_commission_changes"],
        check_html=["commission", "validator", "marinade"],
        must_have_insight=True,
        description="Investment: avoid validators with rising commissions",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # ANALYTICAL — Teknik/analitik sorular
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="analytical_01_how_apy_calculated",
        group="analytical",
        query="How is mSOL APY calculated? Show me the actual start and end price data so I can understand the math.",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "price", "sol"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Analytical: how is mSOL APY calculated?",
    ),
    TestCase(
        id="analytical_02_validator_scoring",
        group="analytical",
        query="Explain how Marinade scores validators — what are the scoring components and how does it affect stake delegation?",
        expected_tools=["marinade_score_breakdown", "marinade_config"],
        check_html=["score", "marinade", "validator"],
        must_have_insight=True,
        description="Analytical: validator scoring system explained",
    ),
    TestCase(
        id="analytical_03_mev_impact",
        group="analytical",
        query="How much of Marinade's staking rewards come from MEV? What's the MEV contribution per epoch?",
        expected_tools=["marinade_epoch_rewards"],
        check_html=["mev", "reward", "marinade", "epoch"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Analytical: MEV contribution to rewards",
    ),
    TestCase(
        id="analytical_04_apy_trend",
        group="analytical",
        query="Is Marinade's native staking APY getting better or worse? Show me the trend over the last 20 epochs.",
        expected_tools=["marinade_native_apy"],
        check_html=["apy", "marinade", "epoch"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Analytical: native APY trend over epochs",
    ),
    TestCase(
        id="analytical_05_cluster_decentralization",
        group="analytical",
        query="How decentralized is the Solana validator cluster? Show Marinade's datacenter concentration data.",
        expected_tools=["marinade_cluster_stats"],
        check_html=["cluster", "datacenter", "marinade"],
        must_have_insight=True,
        description="Analytical: cluster decentralization deep dive",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # COMPARATIVE — comparison questions
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="compare_01_msol_vs_jitosol_full",
        group="comparative",
        query="Full comparison: mSOL (Marinade) vs jitoSOL — APY, TVL, validator count, commission. Which is better?",
        expected_tools=["marinade_msol_apy", "marinade_stats", "jito_stake_pool_stats"],
        check_html=["msol", "jito", "apy", "tvl"],
        must_have_percentage=True,
        must_have_table=True,
        must_have_insight=True,
        description="Comparative: mSOL vs jitoSOL full comparison",
    ),
    TestCase(
        id="compare_02_periods_detailed",
        group="comparative",
        query="Give me mSOL APY for all available periods (7d, 30d, 1y, 2y) and analyze which period shows the best yield. Include price data.",
        expected_tools=["marinade_msol_apy"],
        check_html=["7", "30", "1", "2", "apy", "msol", "%"],
        must_have_percentage=True,
        must_have_table=True,
        must_have_insight=True,
        description="Comparative: all 4 mSOL APY periods with analysis",
    ),
    TestCase(
        id="compare_03_native_vs_liquid",
        group="comparative",
        query="Compare Marinade native staking vs mSOL liquid staking — APY difference, liquidity, flexibility. What should a new user pick?",
        expected_tools=["marinade_native_apy", "marinade_msol_apy"],
        check_html=["native", "msol", "apy", "marinade", "%"],
        must_have_percentage=True,
        must_have_table=True,
        must_have_insight=True,
        description="Comparative: native staking vs mSOL liquid staking",
    ),
    TestCase(
        id="compare_04_top_vs_bottom_validators",
        group="comparative",
        query="Compare the top 5 Marinade validators with the bottom 5 by score. What's the difference in their metrics?",
        expected_tools=["marinade_validators", "marinade_validator_scores"],
        check_html=["validator", "score", "marinade"],
        must_have_table=True,
        must_have_insight=True,
        description="Comparative: top vs bottom validators",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # TURKISH — Turkish phrasings
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="turkish_01_informal",
        group="turkish",
        query="mSOL'e stake etmeli miyim? APY ne kadar?",
        expected_tools=["marinade_msol_apy"],
        check_html=["msol", "apy"],
        must_have_percentage=True,
        description="Türkçe informal: stake etmeli miyim?",
    ),
    TestCase(
        id="turkish_02_compare",
        group="turkish",
        query="mSOL mu jitoSOL mu daha iyi? APY ve TVL karşılaştır.",
        expected_tools=["marinade_msol_apy", "marinade_stats"],
        check_html=["msol", "apy"],
        must_have_percentage=True,
        must_have_table=True,
        description="Türkçe: mSOL vs jitoSOL karşılaştırması",
    ),
    TestCase(
        id="turkish_03_validator",
        group="turkish",
        query="Marinade'daki en iyi validatorlar hangileri? Skor ve komisyon açısından değerlendir.",
        expected_tools=["marinade_validators", "marinade_validator_scores"],
        check_html=["validator", "marinade", "skor"],
        must_have_insight=True,
        description="Türkçe: en iyi validatorlar",
    ),
    TestCase(
        id="turkish_04_native_apy",
        group="turkish",
        query="Marinade native staking APY'si son dönemde nasıl? Yukarı mı aşağı mı gidiyor?",
        expected_tools=["marinade_native_apy"],
        check_html=["apy", "marinade"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Türkçe: native APY trend",
    ),
    TestCase(
        id="turkish_05_how_much_earn",
        group="turkish",
        query="10 SOL'umu Marinade'da 1 yıl stake etsem ne kadar kazanırım?",
        expected_tools=["marinade_msol_apy"],
        check_html=["sol", "msol", "apy"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Türkçe: 10 SOL'dan ne kadar kazanırım?",
    ),
    TestCase(
        id="turkish_06_commission_danger",
        group="turkish",
        query="Son zamanlarda komisyonunu artıran Marinade validatörleri var mı? Risk mi oluşturuyor?",
        expected_tools=["marinade_commission_changes"],
        check_html=["komisyon", "validator", "marinade"],
        must_have_insight=True,
        description="Türkçe: komisyon artışı risk analizi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # EDGE CASES — Belirsiz, zor, multi-step
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="edge_01_ambiguous_apy",
        group="edge",
        query="What's the current mSOL APY?",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        must_have_percentage=True,
        description="Edge: ambiguous 'current APY' — should pick a period",
    ),
    TestCase(
        id="edge_02_what_period",
        group="edge",
        query="Over what time period has mSOL grown the most? Show me the data.",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Edge: which period has highest APY?",
    ),
    TestCase(
        id="edge_03_full_dashboard",
        group="edge",
        query=(
            "Give me a complete Marinade Finance report: "
            "protocol stats, top validators, current APY (native + mSOL), "
            "recent commission changes, and cluster health."
        ),
        expected_tools=["marinade_stats", "marinade_native_apy", "marinade_msol_apy",
                        "marinade_validators", "marinade_commission_changes"],
        check_html=["marinade", "tvl", "apy", "validator", "commission"],
        must_have_percentage=True,
        must_have_insight=True,
        description="Edge: mega full dashboard — many tools",
    ),
    TestCase(
        id="edge_04_validator_deep_multistep",
        group="edge",
        query=(
            "Find the highest-scoring Marinade validator. Then show me its: "
            "commission history, uptime history, and software version history."
        ),
        expected_tools=["marinade_validators", "marinade_validator_commissions",
                        "marinade_validator_uptimes", "marinade_validator_versions"],
        check_html=["validator", "commission", "uptime", "version", "marinade"],
        must_have_insight=True,
        description="Edge: highest-scoring validator full deep dive",
    ),
    TestCase(
        id="edge_05_solana_network_health",
        group="edge",
        query="I want to understand Solana validator network health from Marinade's perspective. Show decentralization and block production data.",
        expected_tools=["marinade_cluster_stats"],
        check_html=["cluster", "marinade", "validator"],
        must_have_insight=True,
        description="Edge: Solana network health via cluster stats",
    ),
    TestCase(
        id="edge_06_next_epoch_strategy",
        group="edge",
        query=(
            "What is Marinade planning to do with its stake next epoch? "
            "Which validators are about to gain or lose delegation? Why?"
        ),
        expected_tools=["marinade_staking_report", "marinade_validator_scores"],
        check_html=["marinade", "stake", "validator"],
        must_have_insight=True,
        description="Edge: next-epoch delegation plan analysis",
    ),
    TestCase(
        id="edge_07_msol_price_not_apy",
        group="edge",
        query="What is mSOL trading at right now? Is it above or below its SOL peg?",
        expected_tools=["marinade_stats"],
        check_html=["msol", "sol", "price"],
        must_have_insight=True,
        description="Edge: mSOL price vs peg — distinct from APY",
    ),
    TestCase(
        id="edge_08_jito_mev_best_validators",
        group="edge",
        query="Which Marinade validators have the best Jito MEV commission setup? I want maximum MEV rewards as a delegator.",
        expected_tools=["marinade_jito_commissions"],
        check_html=["jito", "commission", "validator", "mev"],
        must_have_insight=True,
        description="Edge: best Jito MEV commission validators",
    ),
]


# ─── Test runner ─────────────────────────────────────────────────────────────

async def run_single(case: TestCase, query_fn) -> TestResult:
    t0 = time.time()
    for attempt in range(3):
        try:
            result = await query_fn(case.query)
            quality = html_quality_score(result["html"])
            return TestResult(
                case=case,
                tools_called=result["tools_called"],
                html=result["html"],
                plain=result["plain"],
                duration=time.time() - t0,
                quality=quality,
            )
        except Exception as e:
            err = str(e)
            if "429" in err and attempt < 2:
                wait = 20 + attempt * 15
                print(f"   [429 — {wait}s bekleniyor]")
                await asyncio.sleep(wait)
                continue
            return TestResult(
                case=case,
                tools_called=[],
                html="",
                plain="",
                duration=time.time() - t0,
                quality={},
                error=err,
            )


def print_result(r: TestResult, print_html: bool = False, verbose: bool = True):
    status = "✅" if r.passed else "❌"
    q = r.quality_score

    print(f"\n{'─'*72}")
    print(f"{status} [{r.case.id}] {r.case.description}")
    print(f"   Group    : {r.case.group}")
    print(f"   Query    : {r.case.query[:95]}{'...' if len(r.case.query)>95 else ''}")
    print(f"   Tools    : {r.tools_called}")
    print(f"   Time     : {r.duration:.1f}s  |  HTML: {len(r.html):,} chars  |  Quality: {q}/100")

    if r.error:
        print(f"   HATA     : {r.error[:200]}")
        return

    if verbose:
        # HTML quality breakdown
        qd = r.quality
        icons = []
        if qd.get("has_wrapper"):      icons.append("🏗wrapper")
        if qd.get("has_insight_box"):  icons.append("💡insight")
        if qd.get("has_warning_box"):  icons.append("⚠warning")
        if qd.get("has_table"):        icons.append("📊table")
        if qd.get("has_stat_cards"):   icons.append("🃏cards")
        if qd.get("has_percentage"):   icons.append("📈%")
        if qd.get("not_raw_json"):     icons.append("✍human")
        print(f"   Structure: {' | '.join(icons) or 'WEAK'}")

        # Kelime kontrolleri
        if r.case.check_html:
            kw = r.kw_checks()
            found = [k for k, v in kw.items() if v]
            miss  = [k for k, v in kw.items() if not v]
            if found: print(f"   ✓ Kelime : {found}")
            if miss:  print(f"   ✗ Eksik  : {miss}")

        # Zorunlu kontroller
        checks = []
        if r.case.must_have_percentage:
            ok = r.quality.get("has_percentage", False)
            checks.append(f"{'✅' if ok else '❌'} % var")
        if r.case.must_have_table:
            ok = r.quality.get("has_table", False)
            checks.append(f"{'✅' if ok else '❌'} table var")
        if r.case.must_have_insight:
            ok = r.quality.get("has_insight_box", False)
            checks.append(f"{'✅' if ok else '❌'} insight var")
        if checks:
            print(f"   Zorunlu  : {' | '.join(checks)}")

    # Text preview (HTML stripped)
    plain_preview = re.sub(r"\s+", " ", r.plain[:300]).strip()
    print(f"   Response : {plain_preview}...")

    if print_html:
        print(f"\n{'▼'*72}")
        print("   FULL HTML OUTPUT:")
        print(f"{'▼'*72}")
        print(r.html)
        print(f"{'▲'*72}")


async def run_all(
    filter_id: str = None,
    filter_group: str = None,
    print_html: bool = False,
    verbose: bool = True,
):
    from orchestrator import query as _query

    cases = ALL_CASES
    if filter_id:
        cases = [c for c in cases if c.id == filter_id]
    elif filter_group:
        cases = [c for c in cases if c.group == filter_group]

    print(f"\n{'═'*72}")
    print(f"Marinade LLM extended suite — many phrasings")
    print(f"{'═'*72}")
    print(f"Toplam test: {len(cases)}")
    if filter_id:     print(f"  ID filtresi: {filter_id}")
    if filter_group:  print(f"  Grup filtresi: {filter_group}")

    results: list[TestResult] = []

    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] running {case.id}...", flush=True)
        r = await run_single(case, _query)
        results.append(r)
        print_result(r, print_html=print_html, verbose=verbose)
        if i < len(cases):
            await asyncio.sleep(2)

    # Summary
    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    errors = sum(1 for r in results if r.error)
    avg_q  = sum(r.quality_score for r in results) / total if total else 0
    avg_d  = sum(r.duration for r in results) / total if total else 0
    avg_l  = sum(len(r.html) for r in results) / total if total else 0

    pct_checks = [
        r for r in results
        if r.case.must_have_percentage and not r.error
    ]
    pct_pass = sum(1 for r in pct_checks if r.quality.get("has_percentage"))

    table_checks = [r for r in results if r.case.must_have_table and not r.error]
    table_pass   = sum(1 for r in table_checks if r.quality.get("has_table"))

    insight_checks = [r for r in results if r.case.must_have_insight and not r.error]
    insight_pass   = sum(1 for r in insight_checks if r.quality.get("has_insight_box"))

    print(f"\n{'═'*72}")
    print(f"OVERALL RESULT")
    print(f"{'═'*72}")
    print(f"  ✅ Passed     : {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"  ❌ Hata       : {errors}")
    print(f"  📊 Ort kalite : {avg_q:.0f}/100")
    print(f"  ⏱ Avg time  : {avg_d:.1f}s")
    print(f"  📄 Ort HTML   : {avg_l:,.0f} chars")
    if pct_checks:
        print(f"  📈 shows a %  : {pct_pass}/{len(pct_checks)} ({pct_pass/len(pct_checks)*100:.0f}%)")
    if table_checks:
        print(f"  📊 Tablo      : {table_pass}/{len(table_checks)} ({table_pass/len(table_checks)*100:.0f}%)")
    if insight_checks:
        print(f"  💡 Insight    : {insight_pass}/{len(insight_checks)} ({insight_pass/len(insight_checks)*100:.0f}%)")

    print(f"\n{'─'*72}")
    print("FAILED:")
    failed = [r for r in results if not r.passed]
    if not failed:
        print("  All passed! 🎉")
    else:
        for r in failed:
            reason = r.error or f"Beklenen: {r.case.expected_tools}, Gelen: {r.tools_called}"
            print(f"  ✗ {r.case.id}: {reason[:130]}")

    print(f"\n{'─'*72}")
    print("SUMMARY BY GROUP:")
    from collections import defaultdict
    by_group: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_group[r.case.group].append(r)
    for grp, gres in sorted(by_group.items()):
        ok = sum(1 for r in gres if r.passed)
        avg_gq = sum(r.quality_score for r in gres) / len(gres)
        print(f"  {grp:15s}: {ok}/{len(gres)} ✅  | kalite {avg_gq:.0f}/100")

    print(f"\n{'─'*72}")
    print("QUALITY RANKING (best -> worst):")
    sorted_r = sorted(results, key=lambda r: r.quality_score, reverse=True)
    for r in sorted_r[:10]:
        status = "✅" if r.passed else "❌"
        print(f"  {r.quality_score:3d}/100  {status} {r.case.id:<35} | {len(r.html):,} chars")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--id",         help="Belirli test ID'si")
    parser.add_argument("--group",      help="Grup filtresi: informal, investment, analytical, comparative, turkish, edge")
    parser.add_argument("--print-html", action="store_true", help="Print the full HTML output")
    parser.add_argument("--quiet",      action="store_true", help="Short output")
    args = parser.parse_args()

    asyncio.run(run_all(
        filter_id=args.id,
        filter_group=args.group,
        print_html=args.print_html,
        verbose=not args.quiet,
    ))

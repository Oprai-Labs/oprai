"""
Marinade LLM End-to-End Test Suite
────────────────────────────────────
Tüm 15 Marinade tool'unu tüm parametre kombinasyonları ve uç case'lerle
LLM üzerinden test eder. Hem tool seçimi hem HTML yanıt kalitesi ölçülür.

Çalıştır:
  cd services/defi-query-service
  python3 tests/e2e_llm_marinade.py
  python3 tests/e2e_llm_marinade.py --tool marinade_validators
  python3 tests/e2e_llm_marinade.py --id val_01_top10
  python3 tests/e2e_llm_marinade.py --fast
"""

import asyncio
import re
import sys
import time
import os
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# ─── Test scaffolding ────────────────────────────────────────────────────────

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
    # MARINADE_STATS — mSOL fiyatı, TVL, staking özeti
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="stats_01_msol_price",
        tool="marinade_stats",
        query="What is the current price of mSOL in SOL and USD?",
        expected_tools=["marinade_stats"],
        check_html=["msol", "sol", "price"],
        check_fields=["msol", "sol", "usd"],
        description="mSOL fiyatı SOL ve USD",
    ),
    TestCase(
        id="stats_02_tvl",
        tool="marinade_stats",
        query="What is Marinade Finance's total value locked (TVL)?",
        expected_tools=["marinade_stats"],
        check_html=["marinade", "tvl", "sol"],
        check_fields=["tvl", "staked"],
        description="Marinade TVL sorgusu",
    ),
    TestCase(
        id="stats_03_overview",
        tool="marinade_stats",
        query="Give me a full overview of Marinade Finance — TVL, mSOL price, and staking stats.",
        expected_tools=["marinade_stats"],
        check_html=["marinade", "msol", "sol", "tvl"],
        check_fields=["msol", "tvl", "staked"],
        description="Marinade protokol özeti",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_VALIDATORS — validator listesi, filtreleme, sıralama
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="val_01_top10",
        tool="marinade_validators",
        query="Show me the top 10 validators on Marinade by score.",
        expected_tools=["marinade_validators"],
        check_html=["validator", "marinade", "score"],
        check_fields=["score", "rank", "vote_account"],
        description="Top 10 validator by score",
    ),
    TestCase(
        id="val_02_superminority",
        tool="marinade_validators",
        query="List all superminority validators that Marinade stakes to.",
        expected_tools=["marinade_validators"],
        check_html=["validator", "marinade", "superminority"],
        check_fields=["validator", "stake"],
        description="Superminority validator filtresi",
    ),
    TestCase(
        id="val_03_with_names",
        tool="marinade_validators",
        query="Show me Marinade validators that have names or identities registered.",
        expected_tools=["marinade_validators"],
        check_html=["validator", "marinade"],
        check_fields=["validator"],
        description="İsimli validator filtresi",
    ),
    TestCase(
        id="val_04_with_stake",
        tool="marinade_validators",
        query="Which validators currently receive Marinade delegation/stake?",
        expected_tools=["marinade_validators"],
        check_html=["validator", "marinade", "stake"],
        check_fields=["stake", "validator"],
        description="Marinade stake alan validatorlar",
    ),
    TestCase(
        id="val_05_sort_by_stake",
        tool="marinade_validators",
        query="Sort Marinade validators by activated stake in descending order. Show top 10.",
        expected_tools=["marinade_validators"],
        check_html=["validator", "stake", "marinade"],
        check_fields=["stake", "rank"],
        description="Stake'e göre sıralı validator listesi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_VALIDATOR_SCORES — skor bileşenleri
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="scores_01_top_ranked",
        tool="marinade_validator_scores",
        query="What are the top-ranked validators on Marinade by score? Show their scoring breakdown.",
        expected_tools=["marinade_validator_scores"],
        check_html=["validator", "score", "marinade"],
        check_fields=["score", "rank"],
        description="Top ranked validators skoru",
    ),
    TestCase(
        id="scores_02_eligible_stake",
        tool="marinade_validator_scores",
        query="How much mSOL is each top Marinade validator eligible to receive?",
        expected_tools=["marinade_validator_scores"],
        check_html=["validator", "marinade", "stake"],
        check_fields=["score", "eligible"],
        description="Eligible stake mSOL miktarları",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_SCORE_BREAKDOWN — detaylı skor bileşenleri
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="breakdown_01_all",
        tool="marinade_score_breakdown",
        query="Show me the Marinade validator score breakdown — what are the scoring components and their weights?",
        expected_tools=["marinade_score_breakdown"],
        check_html=["score", "marinade", "validator"],
        check_fields=["score", "commission"],
        description="Tüm validatorlar için skor bileşenleri",
    ),
    TestCase(
        id="breakdown_02_specific_validator",
        tool="marinade_score_breakdown",
        query=(
            "Get the Marinade score breakdown for a specific validator. "
            "First get a validator from the list, then show me its detailed score components."
        ),
        expected_tools=["marinade_score_breakdown", "marinade_validators"],
        check_html=["validator", "score", "marinade"],
        check_fields=["score"],
        description="Spesifik validator skor breakdown (multi-step)",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_VALIDATOR_UPTIMES — uptime geçmişi (identity gerekli)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="uptime_01_specific",
        tool="marinade_validator_uptimes",
        query=(
            "I want to check the uptime history of a Marinade validator. "
            "First find a validator from the Marinade list, then show me its uptime history."
        ),
        expected_tools=["marinade_validator_uptimes", "marinade_validators"],
        check_html=["validator", "uptime", "marinade"],
        check_fields=["uptime", "epoch"],
        description="Validator uptime geçmişi (multi-step)",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_VALIDATOR_COMMISSIONS — komisyon geçmişi (identity gerekli)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="commission_hist_01",
        tool="marinade_validator_commissions",
        query=(
            "Show me the commission history for a top Marinade validator. "
            "Get the validator list first, then look up commission history."
        ),
        expected_tools=["marinade_validator_commissions", "marinade_validators"],
        check_html=["validator", "commission", "marinade"],
        check_fields=["commission", "epoch"],
        description="Validator komisyon geçmişi (multi-step)",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_VALIDATOR_VERSIONS — yazılım versiyon geçmişi (identity)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="versions_01_specific",
        tool="marinade_validator_versions",
        query=(
            "What software version has a Marinade validator been running? "
            "Get the validators list first, then check one validator's version history."
        ),
        expected_tools=["marinade_validator_versions", "marinade_validators"],
        check_html=["validator", "version", "marinade"],
        check_fields=["version", "epoch"],
        description="Validator versiyon geçmişi (multi-step)",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_CLUSTER_STATS — küme sağlığı, datacenter yoğunluğu
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="cluster_01_health",
        tool="marinade_cluster_stats",
        query="How healthy is the Solana validator cluster according to Marinade? Show datacenter concentration.",
        expected_tools=["marinade_cluster_stats"],
        check_html=["cluster", "marinade", "datacenter"],
        check_fields=["epoch", "datacenter"],
        description="Küme sağlığı ve datacenter yoğunluğu",
    ),
    TestCase(
        id="cluster_02_decentralization",
        tool="marinade_cluster_stats",
        query="How decentralized is Solana? Show block production stats from Marinade's cluster data.",
        expected_tools=["marinade_cluster_stats"],
        check_html=["marinade", "cluster", "block"],
        check_fields=["epoch", "block"],
        description="Blok üretimi ve merkeziyetsizlik analizi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_EPOCH_REWARDS — epoch başına ödüller (MEV, inflation)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="rewards_01_overview",
        tool="marinade_epoch_rewards",
        query="What are the staking rewards per epoch on Marinade? Break down MEV vs inflation rewards.",
        expected_tools=["marinade_epoch_rewards"],
        check_html=["reward", "marinade", "mev", "epoch"],
        check_fields=["reward", "epoch", "mev"],
        description="Epoch başına staking ödülleri",
    ),
    TestCase(
        id="rewards_02_mev_share",
        tool="marinade_epoch_rewards",
        query="How much of Marinade's staking rewards come from MEV vs regular block rewards?",
        expected_tools=["marinade_epoch_rewards"],
        check_html=["mev", "reward", "epoch"],
        check_fields=["mev", "reward"],
        description="MEV vs inflation ödül oranı",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_STAKING_REPORT — sonraki epoch delegasyon planı
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="stake_report_01_gainers",
        tool="marinade_staking_report",
        query="Which validators will receive more Marinade stake in the next epoch? Show top gainers.",
        expected_tools=["marinade_staking_report"],
        check_html=["marinade", "stake", "validator"],
        check_fields=["stake", "validator"],
        description="Sonraki epoch en çok stake kazanan validatorlar",
    ),
    TestCase(
        id="stake_report_02_losers",
        tool="marinade_staking_report",
        query="Which validators will lose Marinade delegation in the next epoch?",
        expected_tools=["marinade_staking_report"],
        check_html=["marinade", "stake", "validator"],
        check_fields=["stake"],
        description="Sonraki epoch stake kaybeden validatorlar",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_SCORING_REPORTS — geçmiş scoring raporları
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="scoring_reports_01",
        tool="marinade_scoring_reports",
        query="Are there any historical scoring reports from Marinade? Show me the available reports.",
        expected_tools=["marinade_scoring_reports"],
        check_html=["marinade", "report", "scoring"],
        check_fields=["report", "epoch"],
        description="Marinade scoring raporları listesi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_COMMISSION_CHANGES — komisyon değişiklik kayıtları
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="commission_changes_01_all",
        tool="marinade_commission_changes",
        query="Which validators have changed their commission recently on the Marinade network?",
        expected_tools=["marinade_commission_changes"],
        check_html=["commission", "validator", "marinade"],
        check_fields=["commission", "validator", "epoch"],
        description="Tüm komisyon değişiklik kayıtları",
    ),
    TestCase(
        id="commission_changes_02_increases",
        tool="marinade_commission_changes",
        query="Have any Marinade validators raised their commission? I want to check for commission increases.",
        expected_tools=["marinade_commission_changes"],
        check_html=["commission", "marinade", "validator"],
        check_fields=["commission"],
        description="Komisyon artışı tespiti",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_CONFIG — sistem konfigürasyonu
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="config_01_system",
        tool="marinade_config",
        query="What are the Marinade system configuration and program addresses?",
        expected_tools=["marinade_config"],
        check_html=["marinade", "config"],
        check_fields=["config"],
        description="Marinade sistem konfigürasyonu",
    ),
    TestCase(
        id="config_02_scoring_params",
        tool="marinade_config",
        query="What scoring parameters does Marinade use for its delegation strategy?",
        expected_tools=["marinade_config"],
        check_html=["marinade", "scoring", "config"],
        check_fields=["scoring"],
        description="Marinade scoring parametreleri",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_MSOL_APY — dönem bazlı mSOL APY (7d / 30d / 1y / 2y)
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="msol_apy_01_1y",
        tool="marinade_msol_apy",
        query="What is the mSOL APY over the past year on Marinade?",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        check_fields=["apy", "year"],
        description="mSOL APY 1 yıllık",
    ),
    TestCase(
        id="msol_apy_02_30d",
        tool="marinade_msol_apy",
        query="What is the 30-day APY for mSOL? How has it been performing recently?",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        check_fields=["apy", "30"],
        description="mSOL APY 30 günlük",
    ),
    TestCase(
        id="msol_apy_03_7d",
        tool="marinade_msol_apy",
        query="Show me mSOL's 7-day APY. What's the very recent yield been?",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        check_fields=["apy", "7"],
        description="mSOL APY 7 günlük",
    ),
    TestCase(
        id="msol_apy_04_2y",
        tool="marinade_msol_apy",
        query="What has mSOL's 2-year annualized APY been? Show the price growth over 2 years.",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        check_fields=["apy", "2"],
        description="mSOL APY 2 yıllık + price growth",
    ),
    TestCase(
        id="msol_apy_05_compare_periods",
        tool="marinade_msol_apy",
        query="Compare mSOL APY across different timeframes: 7 days, 30 days, 1 year, and 2 years.",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol", "%"],
        check_fields=["apy"],
        description="Tüm dönemler karşılaştırması (4 tool call)",
    ),
    TestCase(
        id="msol_apy_06_vs_jitosol",
        tool="marinade_msol_apy",
        query="Is mSOL staking worth it? Compare 1-year mSOL APY vs Jito's jitoSOL APY.",
        expected_tools=["marinade_msol_apy", "jito_stake_pool_stats"],
        check_html=["apy", "msol", "jito", "%"],
        check_fields=["apy"],
        description="mSOL APY vs jitoSOL APY karşılaştırması",
    ),
    TestCase(
        id="msol_apy_07_price_growth",
        tool="marinade_msol_apy",
        query="How much has the mSOL/SOL exchange rate grown over the past year? Show start and end price.",
        expected_tools=["marinade_msol_apy"],
        check_html=["msol", "sol", "price"],
        check_fields=["price", "sol"],
        description="mSOL/SOL fiyat büyümesi 1 yıl",
    ),
    TestCase(
        id="msol_apy_08_turkish",
        tool="marinade_msol_apy",
        query="mSOL'ün son 1 yıllık APY'si nedir? Türkçe açıkla.",
        expected_tools=["marinade_msol_apy"],
        check_html=["apy", "msol"],
        check_fields=["apy"],
        description="Türkçe sorgu — dil kuralı testi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_NATIVE_APY — native staking APY geçmişi
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="native_apy_01_current",
        tool="marinade_native_apy",
        query="What is the current native staking APY on Marinade? Show recent epochs.",
        expected_tools=["marinade_native_apy"],
        check_html=["apy", "marinade", "%"],
        check_fields=["apy", "epoch"],
        description="Güncel native staking APY",
    ),
    TestCase(
        id="native_apy_02_trend",
        tool="marinade_native_apy",
        query="Is Marinade's native staking APY trending up or down over the past months?",
        expected_tools=["marinade_native_apy"],
        check_html=["apy", "marinade", "epoch"],
        check_fields=["apy", "epoch"],
        description="Native APY trend analizi",
    ),
    TestCase(
        id="native_apy_03_vs_msol_apy",
        tool="marinade_native_apy",
        query="What's the difference between Marinade native staking APY and mSOL APY? Show both.",
        expected_tools=["marinade_native_apy", "marinade_msol_apy"],
        check_html=["apy", "marinade", "%"],
        check_fields=["apy"],
        description="Native APY vs mSOL APY karşılaştırması",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # MARINADE_JITO_COMMISSIONS — Jito MEV komisyon verileri
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="jito_comm_01_lowest",
        tool="marinade_jito_commissions",
        query="Which Marinade validators have the lowest Jito MEV commission?",
        expected_tools=["marinade_jito_commissions"],
        check_html=["validator", "commission", "jito"],
        check_fields=["commission", "validator"],
        description="En düşük Jito MEV komisyonu",
    ),
    TestCase(
        id="jito_comm_02_best_for_delegators",
        tool="marinade_jito_commissions",
        query="From a delegator's perspective, which validators on Marinade have the best MEV commission setup?",
        expected_tools=["marinade_jito_commissions"],
        check_html=["commission", "jito", "validator"],
        check_fields=["commission", "mev"],
        description="Delegatörler için en iyi MEV komisyon yapısı",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # EDGE CASES — çapraz protokol, karşılaştırma, hata yönetimi
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="edge_01_msol_vs_jitosol",
        tool="marinade_stats",
        query="Compare mSOL (Marinade) vs jitoSOL staking — which has better APY and TVL?",
        expected_tools=["marinade_stats", "marinade_native_apy", "jito_stake_pool_stats"],
        check_html=["msol", "jitosol", "apy", "tvl"],
        check_fields=["apy", "tvl"],
        description="mSOL vs jitoSOL karşılaştırma",
    ),
    TestCase(
        id="edge_02_best_marinade_validator",
        tool="marinade_validators",
        query=(
            "Which Marinade validator should I choose for native staking? "
            "Compare commission, uptime and score. Pick the best one."
        ),
        expected_tools=["marinade_validators", "marinade_validator_scores"],
        check_html=["validator", "marinade", "commission", "score"],
        check_fields=["validator", "commission", "score"],
        description="En iyi validator seçimi — çok kriterli (multi-step)",
    ),
    TestCase(
        id="edge_03_full_marinade_overview",
        tool="marinade_stats",
        query=(
            "Give me a complete Marinade Finance dashboard: "
            "mSOL price, TVL, top validators, current APY, and any recent commission changes."
        ),
        expected_tools=["marinade_stats", "marinade_native_apy", "marinade_validators"],
        check_html=["marinade", "msol", "tvl", "apy", "validator"],
        check_fields=["msol", "apy", "tvl"],
        description="Tam Marinade dashboard — çok tool",
    ),
    TestCase(
        id="edge_04_delegation_strategy",
        tool="marinade_staking_report",
        query=(
            "Explain Marinade's delegation strategy for next epoch. "
            "Which validators are gaining stake and why might that be?"
        ),
        expected_tools=["marinade_staking_report", "marinade_validator_scores"],
        check_html=["marinade", "stake", "validator", "delegation"],
        check_fields=["stake", "validator"],
        description="Delegasyon stratejisi analizi (multi-step)",
    ),
    TestCase(
        id="edge_05_validator_deep_dive",
        tool="marinade_validators",
        query=(
            "Do a deep dive on the top Marinade validator: "
            "get its score breakdown, commission history, and uptime."
        ),
        expected_tools=["marinade_validators", "marinade_score_breakdown",
                        "marinade_validator_commissions", "marinade_validator_uptimes"],
        check_html=["validator", "score", "commission", "uptime"],
        check_fields=["score", "commission"],
        description="En iyi validator tam analiz (multi-step 4 tool)",
    ),
    TestCase(
        id="edge_06_cluster_decentralization",
        tool="marinade_cluster_stats",
        query=(
            "How decentralized is the Solana network? Show Marinade's cluster stats "
            "and cross-reference with the validator score data."
        ),
        expected_tools=["marinade_cluster_stats", "marinade_validator_scores"],
        check_html=["cluster", "validator", "marinade", "datacenter"],
        check_fields=["epoch", "datacenter"],
        description="Küme merkeziyetsizliği — cluster + score",
    ),
    TestCase(
        id="edge_07_staking_comparison",
        tool="marinade_native_apy",
        query=(
            "I want to stake SOL. Compare Marinade native staking APY vs mSOL liquid staking "
            "vs Jito — which is best right now?"
        ),
        expected_tools=["marinade_native_apy", "marinade_stats", "jito_stake_pool_stats"],
        check_html=["apy", "marinade", "sol", "staking", "%"],
        check_fields=["apy"],
        description="Native vs liquid staking karşılaştırması",
    ),
]


# ─── Test runner ─────────────────────────────────────────────────────────────

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
    print(f"   Time     : {r.duration:.1f}s  |  HTML: {len(r.html)} chars")

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
    print(f"   Response : {plain_preview}")


async def run_all(filter_val: Optional[str] = None, fast: bool = False):
    from orchestrator import query as _query

    cases = ALL_CASES
    if filter_val:
        cases = [c for c in cases if c.tool == filter_val or c.id == filter_val]

    print(f"\n{'═'*70}")
    print(f"Marinade LLM End-to-End Test Suite")
    print(f"{'═'*70}")
    print(f"Toplam test: {len(cases)} | Mod: {'HIZLI' if fast else 'DETAYLI'}")
    if filter_val:
        print(f"Filtre: {filter_val}")

    results: list[TestResult] = []

    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] running {case.id}...", flush=True)
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
    print(f"OVERALL RESULT")
    print(f"{'═'*70}")
    print(f"  Toplam test   : {total}")
    print(f"  ✅ Passed     : {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"  🎯 Right tool : {tool_ok}/{total} ({tool_ok/total*100:.0f}%)")
    print(f"  ❌ Hata       : {errors}")
    print(f"  ⏱ Avg time  : {avg_dur:.1f}s/test")
    print(f"  📄 Ort HTML   : {avg_html:.0f} karakter")
    print(f"  📊 Yorum kap. : {avg_cov*100:.0f}%")

    print(f"\n{'─'*70}")
    print("FAILED TESTS:")
    failed = [r for r in results if not r.passed]
    if not failed:
        print("  All passed! 🎉")
    else:
        for r in failed:
            reason = r.error or f"Beklenen: {r.case.expected_tools}, Gelen: {r.tools_called}"
            print(f"  ✗ {r.case.id}: {reason[:130]}")

    print(f"\n{'─'*70}")
    print("PER-TOOL SUMMARY:")
    from collections import defaultdict
    by_tool: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_tool[r.case.tool].append(r)
    for tool, tool_results in sorted(by_tool.items()):
        ok = sum(1 for r in tool_results if r.passed)
        print(f"  {tool:44s}: {ok}/{len(tool_results)} ✅")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", help="Sadece belirli bir tool (ör: marinade_validators)")
    parser.add_argument("--id",   help="Belirli bir test ID'si (ör: val_01_top10)")
    parser.add_argument("--fast", action="store_true", help="Kısa çıktı modu")
    args = parser.parse_args()

    asyncio.run(run_all(args.id or args.tool, args.fast))

"""
Marinade Full Coverage LLM Test Suite
──────────────────────────────────────
21 tool × 5-10 case = 150+ farklı soru tarzı.
Her tool için birden fazla case: resmi, informal, Türkçe, karşılaştırmalı, wallet-spesifik.

Çalıştır:
  cd services/defi-query-service
  python3 tests/e2e_llm_marinade_full.py
  python3 tests/e2e_llm_marinade_full.py --tool marinade_stats
  python3 tests/e2e_llm_marinade_full.py --id stats_01
  python3 tests/e2e_llm_marinade_full.py --group snapshot
  python3 tests/e2e_llm_marinade_full.py --print-html --id votes_msol_01
"""

import asyncio
import re
import sys
import os
import time
import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SNAPSHOT_WALLET = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"  # has mSOL in snapshot


# ─── Kalite araçları ─────────────────────────────────────────────────────────

def strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)

def kw_check(html: str, *words: str) -> dict[str, bool]:
    plain = strip(html).lower()
    return {w: w.lower() in plain for w in words}

def quality(html: str) -> dict:
    s = 0
    d = {}
    d["wrapper"]    = "font-family" in html or 'style="' in html;    s += 15 * d["wrapper"]
    d["insight"]    = "💡" in html or "insight" in html.lower();      s += 15 * d["insight"]
    d["warning"]    = "⚠" in html or "warning" in html.lower();       s += 5  * d["warning"]
    d["table"]      = "<table" in html;                               s += 10 * d["table"]
    d["cards"]      = "border-radius" in html and "padding" in html;  s += 10 * d["cards"]
    d["percent"]    = bool(re.search(r"[\d.]+\s*%", html));           s += 15 * d["percent"]
    d["numbers"]    = bool(re.search(r"\b\d[\d,.]+\b", strip(html))); s += 10 * d["numbers"]
    d["not_raw"]    = "{" not in strip(html)[:150];                   s += 10 * d["not_raw"]
    d["length_ok"]  = len(html) > 600;                                s += 10 * d["length_ok"]
    d["score"]      = s
    return d


# ─── Test veri yapısı ────────────────────────────────────────────────────────

@dataclass
class Case:
    id: str
    tool: str
    group: str
    query: str
    expected: list[str]
    kw: list[str] = field(default_factory=list)
    need_pct: bool = False
    need_table: bool = False
    need_insight: bool = False
    desc: str = ""

@dataclass
class Result:
    case: Case
    tools: list[str]
    html: str
    plain: str
    dur: float
    q: dict
    err: Optional[str] = None

    @property
    def passed(self) -> bool:
        return not self.err and any(t in self.tools for t in self.case.expected)

    @property
    def score(self) -> int:
        return self.q.get("score", 0)


# ─── 150+ test case'i ────────────────────────────────────────────────────────

CASES: list[Case] = [

    # ══════════════════════════════════════════════════════
    # 1. MARINADE_STATS (6 case)
    # ══════════════════════════════════════════════════════
    Case("stats_01", "marinade_stats", "stats",
         "What is the current mSOL price in SOL and in USD?",
         ["marinade_stats"], ["msol","sol","price"], True, False, True,
         "mSOL fiyatı SOL+USD"),
    Case("stats_02", "marinade_stats", "stats",
         "How much SOL is locked in Marinade Finance right now? Show TVL.",
         ["marinade_stats"], ["tvl","marinade","sol"], False, False, True,
         "TVL sorgusu"),
    Case("stats_03", "marinade_stats", "stats",
         "Give me a full Marinade overview — TVL, mSOL price, staking metrics.",
         ["marinade_stats"], ["tvl","msol","marinade"], True, True, True,
         "Tam overview"),
    Case("stats_04", "marinade_stats", "stats",
         "mSOL fiyatı ne kadar? SOL cinsinden söyle.",
         ["marinade_stats"], ["msol","sol"], False, False, True,
         "Türkçe mSOL fiyat"),
    Case("stats_05", "marinade_stats", "stats",
         "Is Marinade Finance large in terms of TVL compared to other liquid staking protocols?",
         ["marinade_stats","jito_stake_pool_stats"], ["tvl","marinade"], False, False, True,
         "TVL karşılaştırma"),
    Case("stats_06", "marinade_stats", "stats",
         "How many SOL tokens are staked through Marinade?",
         ["marinade_stats"], ["sol","staked","marinade"], False, False, True,
         "Toplam stake edilen SOL"),

    # ══════════════════════════════════════════════════════
    # 2. MARINADE_MSOL_APY (8 case)
    # ══════════════════════════════════════════════════════
    Case("apy_01", "marinade_msol_apy", "apy",
         "What is the 1-year mSOL APY?",
         ["marinade_msol_apy"], ["apy","msol","%"], True, False, False,
         "1y APY"),
    Case("apy_02", "marinade_msol_apy", "apy",
         "Show me 30-day mSOL APY and the price change over that period.",
         ["marinade_msol_apy"], ["apy","msol","price","%"], True, False, True,
         "30d APY + price"),
    Case("apy_03", "marinade_msol_apy", "apy",
         "What's the 7-day mSOL yield? How has mSOL performed very recently?",
         ["marinade_msol_apy"], ["apy","msol","%"], True, False, True,
         "7d APY"),
    Case("apy_04", "marinade_msol_apy", "apy",
         "mSOL 2 yıllık APY'si nedir? Fiyat değişimini de göster.",
         ["marinade_msol_apy"], ["apy","msol"], True, False, True,
         "Türkçe 2y APY"),
    Case("apy_05", "marinade_msol_apy", "apy",
         "Compare mSOL APY across 7d, 30d, 1y, 2y — which period gives the best annualized return?",
         ["marinade_msol_apy"], ["apy","msol","%"], True, True, True,
         "Tüm dönemler karşılaştırması"),
    Case("apy_06", "marinade_msol_apy", "apy",
         "If I hold mSOL for one year, what is the expected annualized return based on historical data?",
         ["marinade_msol_apy"], ["apy","msol","%"], True, False, True,
         "Beklenen getiri sorusu"),
    Case("apy_07", "marinade_msol_apy", "apy",
         "How has the mSOL/SOL exchange rate changed over the past year? Show start and end price.",
         ["marinade_msol_apy"], ["msol","sol","price"], True, False, True,
         "mSOL/SOL fiyat büyümesi"),
    Case("apy_08", "marinade_msol_apy", "apy",
         "I have 50 SOL to stake. Based on 1-year mSOL APY, how much would I earn in a year?",
         ["marinade_msol_apy"], ["apy","sol","%"], True, False, True,
         "50 SOL kazanç tahmini"),

    # ══════════════════════════════════════════════════════
    # 3. MARINADE_VALIDATORS (7 case)
    # ══════════════════════════════════════════════════════
    Case("val_01", "marinade_validators", "validators",
         "Show me the top 10 Marinade validators ranked by score.",
         ["marinade_validators"], ["validator","score","marinade"], False, True, True,
         "Top 10 validator by score"),
    Case("val_02", "marinade_validators", "validators",
         "Which validators currently receive Marinade stake delegation?",
         ["marinade_validators"], ["validator","stake","marinade"], False, True, True,
         "Stake alan validatorlar"),
    Case("val_03", "marinade_validators", "validators",
         "List Marinade validators that are in the superminority.",
         ["marinade_validators"], ["validator","superminority"], False, True, False,
         "Superminority validatorlar"),
    Case("val_04", "marinade_validators", "validators",
         "Show top Marinade validators sorted by the amount of activated stake.",
         ["marinade_validators"], ["validator","stake"], False, True, False,
         "Stake'e göre sıralama"),
    Case("val_05", "marinade_validators", "validators",
         "Marinade'daki en iyi 5 validator'u göster. Skor ve stake miktarlarını karşılaştır.",
         ["marinade_validators"], ["validator","marinade"], False, True, True,
         "Türkçe top 5"),
    Case("val_06", "marinade_validators", "validators",
         "How many validators does Marinade delegate to? Give me a count and a sample.",
         ["marinade_validators"], ["validator","marinade"], False, False, True,
         "Validator sayısı ve örnek"),
    Case("val_07", "marinade_validators", "validators",
         "Which Marinade validator has the highest score right now?",
         ["marinade_validators","marinade_validator_scores"], ["validator","score"], False, False, True,
         "En yüksek skorlu validator"),

    # ══════════════════════════════════════════════════════
    # 4. MARINADE_VALIDATOR_SCORES (6 case)
    # ══════════════════════════════════════════════════════
    Case("scores_01", "marinade_validator_scores", "scores",
         "Show me the top-ranked validators on Marinade by composite score.",
         ["marinade_validator_scores"], ["score","validator","rank"], False, True, True,
         "Top ranked validators"),
    Case("scores_02", "marinade_validator_scores", "scores",
         "How much mSOL is each top Marinade validator eligible to receive per epoch?",
         ["marinade_validator_scores"], ["score","validator","eligible"], False, True, True,
         "Eligible stake mSOL miktarları"),
    Case("scores_03", "marinade_validator_scores", "scores",
         "What score does the best Marinade validator have? How does it compare to the median?",
         ["marinade_validator_scores"], ["score","validator"], False, False, True,
         "En iyi vs medyan skor"),
    Case("scores_04", "marinade_validator_scores", "scores",
         "List the bottom 5 Marinade validators by score. Why might they be low?",
         ["marinade_validator_scores"], ["score","validator"], False, True, True,
         "En düşük skorlu validatorlar"),
    Case("scores_05", "marinade_validator_scores", "scores",
         "Marinade scoring'de en yüksek ve en düşük skorlu validator'ları karşılaştır.",
         ["marinade_validator_scores"], ["validator","score"], False, True, True,
         "Türkçe: en iyi vs en kötü"),
    Case("scores_06", "marinade_validator_scores", "scores",
         "Which Marinade validators are close to the scoring threshold for losing their delegation?",
         ["marinade_validator_scores"], ["score","validator","marinade"], False, True, True,
         "Eşiğe yakın validatorlar"),

    # ══════════════════════════════════════════════════════
    # 5. MARINADE_SCORE_BREAKDOWN (5 case)
    # ══════════════════════════════════════════════════════
    Case("breakdown_01", "marinade_score_breakdown", "breakdown",
         "Show me the Marinade validator score breakdown — what components make up the score?",
         ["marinade_score_breakdown"], ["score","commission","marinade"], False, True, True,
         "Genel skor bileşenleri"),
    Case("breakdown_02", "marinade_score_breakdown", "breakdown",
         "What is the commission score component for Marinade validators? How much weight does it have?",
         ["marinade_score_breakdown"], ["score","commission"], False, False, True,
         "Komisyon score ağırlığı"),
    Case("breakdown_03", "marinade_score_breakdown", "breakdown",
         "Get the score breakdown for the highest-scoring Marinade validator.",
         ["marinade_score_breakdown","marinade_validators","marinade_validator_scores"],
         ["score","validator"], False, True, True,
         "En iyi validator skor detayı (multi-step)"),
    Case("breakdown_04", "marinade_score_breakdown", "breakdown",
         "Which scoring component hurts Marinade validators the most on average?",
         ["marinade_score_breakdown"], ["score","commission","component"], False, False, True,
         "En kötü skor bileşeni analizi"),
    Case("breakdown_05", "marinade_score_breakdown", "breakdown",
         "Marinade'ın validator puanlama sistemi nasıl çalışıyor? Bileşenlerini açıkla.",
         ["marinade_score_breakdown","marinade_config"], ["score","marinade"], False, True, True,
         "Türkçe: skor sistemi açıklama"),

    # ══════════════════════════════════════════════════════
    # 6. MARINADE_VALIDATOR_UPTIMES (5 case)
    # ══════════════════════════════════════════════════════
    Case("uptime_01", "marinade_validator_uptimes", "uptimes",
         "Check the uptime history of the top Marinade validator. Find it first, then show uptime.",
         ["marinade_validator_uptimes","marinade_validators"],
         ["uptime","validator","epoch"], False, False, True,
         "Top validator uptime (multi-step)"),
    Case("uptime_02", "marinade_validator_uptimes", "uptimes",
         "Which Marinade validators have had downtime recently? Find one and check its uptime history.",
         ["marinade_validator_uptimes","marinade_validators"],
         ["uptime","validator"], False, False, True,
         "Downtime analizi"),
    Case("uptime_03", "marinade_validator_uptimes", "uptimes",
         "Show me the recent epoch-by-epoch uptime for a high-scoring Marinade validator.",
         ["marinade_validator_uptimes","marinade_validators","marinade_validator_scores"],
         ["uptime","epoch","validator"], False, True, True,
         "Epoch bazlı uptime (multi-step)"),
    Case("uptime_04", "marinade_validator_uptimes", "uptimes",
         "Marinade'daki bir validator'ın uptime geçmişini göster. Önce validator listesinden birini seç.",
         ["marinade_validator_uptimes","marinade_validators"],
         ["validator","uptime"], False, False, True,
         "Türkçe uptime geçmişi"),
    Case("uptime_05", "marinade_validator_uptimes", "uptimes",
         "How reliable has the top Marinade validator been? Show its leader slot history.",
         ["marinade_validator_uptimes","marinade_validators"],
         ["validator","uptime","slot"], False, True, True,
         "Güvenilirlik / leader slot analizi"),

    # ══════════════════════════════════════════════════════
    # 7. MARINADE_VALIDATOR_COMMISSIONS (5 case)
    # ══════════════════════════════════════════════════════
    Case("comm_hist_01", "marinade_validator_commissions", "comm_hist",
         "Show the commission history for the top Marinade validator. Has it changed recently?",
         ["marinade_validator_commissions","marinade_validators"],
         ["commission","validator","epoch"], False, True, True,
         "Top validator komisyon geçmişi"),
    Case("comm_hist_02", "marinade_validator_commissions", "comm_hist",
         "Find a Marinade validator and show me if it has ever raised its commission.",
         ["marinade_validator_commissions","marinade_validators"],
         ["commission","validator"], False, False, True,
         "Komisyon artışı tespiti"),
    Case("comm_hist_03", "marinade_validator_commissions", "comm_hist",
         "Has any top Marinade validator kept a stable commission over the past months?",
         ["marinade_validator_commissions","marinade_validators"],
         ["commission","epoch","validator"], False, True, True,
         "Stabil komisyon analizi"),
    Case("comm_hist_04", "marinade_validator_commissions", "comm_hist",
         "En yüksek puanlı Marinade validatorunun komisyon değişim geçmişini göster.",
         ["marinade_validator_commissions","marinade_validators"],
         ["commission","validator"], False, False, True,
         "Türkçe komisyon geçmişi"),
    Case("comm_hist_05", "marinade_validator_commissions", "comm_hist",
         "Pick the top Marinade validator and show its commission history. Is the commission trending up or down?",
         ["marinade_validator_commissions","marinade_validators"],
         ["commission","validator","epoch"], False, True, True,
         "Komisyon trend analizi"),

    # ══════════════════════════════════════════════════════
    # 8. MARINADE_VALIDATOR_VERSIONS (5 case)
    # ══════════════════════════════════════════════════════
    Case("ver_01", "marinade_validator_versions", "versions",
         "What software version is the top Marinade validator running? Show its version history.",
         ["marinade_validator_versions","marinade_validators"],
         ["version","validator","epoch"], False, True, True,
         "Top validator yazılım versiyonu"),
    Case("ver_02", "marinade_validator_versions", "versions",
         "Has the top Marinade validator been keeping up with software upgrades?",
         ["marinade_validator_versions","marinade_validators"],
         ["version","validator"], False, False, True,
         "Yazılım güncel mi?"),
    Case("ver_03", "marinade_validator_versions", "versions",
         "Find a Marinade validator and show what Solana client versions it has used historically.",
         ["marinade_validator_versions","marinade_validators"],
         ["version","validator","epoch"], False, True, True,
         "Tarihsel version geçmişi"),
    Case("ver_04", "marinade_validator_versions", "versions",
         "Marinade'daki en yüksek skorlu validator hangi yazılım versiyonunu kullanıyor? Geçmiş versiyonlarını göster.",
         ["marinade_validator_versions","marinade_validators"],
         ["version","validator"], False, False, True,
         "Türkçe yazılım versiyonu"),
    Case("ver_05", "marinade_validator_versions", "versions",
         "Is the top Marinade validator running the latest Solana client version?",
         ["marinade_validator_versions","marinade_validators"],
         ["version","validator"], False, False, True,
         "Güncel versiyon mu?"),

    # ══════════════════════════════════════════════════════
    # 9. MARINADE_CLUSTER_STATS (6 case)
    # ══════════════════════════════════════════════════════
    Case("cluster_01", "marinade_cluster_stats", "cluster",
         "How healthy is the Solana validator cluster? Show Marinade's cluster stats.",
         ["marinade_cluster_stats"], ["cluster","marinade","epoch"], False, True, True,
         "Küme sağlığı"),
    Case("cluster_02", "marinade_cluster_stats", "cluster",
         "Show the datacenter concentration of the Solana validator set from Marinade's data.",
         ["marinade_cluster_stats"], ["datacenter","marinade","concentration"], False, True, True,
         "Datacenter yoğunluğu"),
    Case("cluster_03", "marinade_cluster_stats", "cluster",
         "How decentralized is Solana's validator network? Show ASO concentration.",
         ["marinade_cluster_stats"], ["datacenter","marinade"], False, True, True,
         "ASO merkeziyetsizliği"),
    Case("cluster_04", "marinade_cluster_stats", "cluster",
         "What are the block production statistics for the Solana cluster recently?",
         ["marinade_cluster_stats"], ["block","cluster","epoch"], False, True, True,
         "Blok üretim istatistikleri"),
    Case("cluster_05", "marinade_cluster_stats", "cluster",
         "Marinade'ın küme verilerine göre Solana ne kadar merkezi?",
         ["marinade_cluster_stats"], ["marinade","cluster"], False, True, True,
         "Türkçe merkeziyetsizlik"),
    Case("cluster_06", "marinade_cluster_stats", "cluster",
         "Is Solana's validator network at risk of centralization? Show the concentration by city.",
         ["marinade_cluster_stats"], ["cluster","datacenter","city"], False, True, True,
         "Şehir bazlı yoğunluk analizi"),

    # ══════════════════════════════════════════════════════
    # 10. MARINADE_EPOCH_REWARDS (6 case)
    # ══════════════════════════════════════════════════════
    Case("rewards_01", "marinade_epoch_rewards", "rewards",
         "What are the staking rewards per epoch on Marinade? Break down MEV vs inflation.",
         ["marinade_epoch_rewards"], ["reward","mev","epoch","marinade"], True, True, True,
         "Epoch başına ödül dağılımı"),
    Case("rewards_02", "marinade_epoch_rewards", "rewards",
         "How much of Marinade's rewards come from MEV vs regular block rewards?",
         ["marinade_epoch_rewards"], ["mev","reward","inflation"], True, False, True,
         "MEV vs inflation oranı"),
    Case("rewards_03", "marinade_epoch_rewards", "rewards",
         "Show me the Marinade epoch reward history. Is MEV contribution growing?",
         ["marinade_epoch_rewards"], ["mev","reward","epoch"], True, True, True,
         "MEV trend analizi"),
    Case("rewards_04", "marinade_epoch_rewards", "rewards",
         "Marinade'da her epoch ne kadar MEV ödülü kazanılıyor? Son epoch'ları göster.",
         ["marinade_epoch_rewards"], ["mev","reward","epoch"], True, True, True,
         "Türkçe MEV ödülleri"),
    Case("rewards_05", "marinade_epoch_rewards", "rewards",
         "What percentage of Marinade staking rewards are from Jito MEV vs standard inflation?",
         ["marinade_epoch_rewards"], ["mev","jito","inflation","reward"], True, False, True,
         "Jito MEV yüzdesi"),
    Case("rewards_06", "marinade_epoch_rewards", "rewards",
         "Compare the last few Marinade epochs: which had the highest staking rewards total?",
         ["marinade_epoch_rewards"], ["epoch","reward","marinade"], False, True, True,
         "Epoch bazlı en yüksek ödül"),

    # ══════════════════════════════════════════════════════
    # 11. MARINADE_STAKING_REPORT (6 case)
    # ══════════════════════════════════════════════════════
    Case("stake_rpt_01", "marinade_staking_report", "staking_report",
         "Which validators will gain Marinade stake in the next epoch? Show top gainers.",
         ["marinade_staking_report"], ["stake","validator","marinade"], False, True, True,
         "Sonraki epoch stake kazananlar"),
    Case("stake_rpt_02", "marinade_staking_report", "staking_report",
         "Which validators are about to lose Marinade delegation next epoch?",
         ["marinade_staking_report"], ["stake","validator","marinade"], False, True, True,
         "Stake kaybedenler"),
    Case("stake_rpt_03", "marinade_staking_report", "staking_report",
         "Show Marinade's planned delegation changes for the upcoming epoch.",
         ["marinade_staking_report"], ["stake","epoch","marinade","delegation"], False, True, True,
         "Planlı delegasyon değişiklikleri"),
    Case("stake_rpt_04", "marinade_staking_report", "staking_report",
         "Hangi validatorlar bir sonraki epoch'ta Marinade stake'i kaybedecek? Neden?",
         ["marinade_staking_report","marinade_validator_scores"],
         ["stake","validator"], False, True, True,
         "Türkçe stake kaybı analizi"),
    Case("stake_rpt_05", "marinade_staking_report", "staking_report",
         "What is the largest single stake change Marinade is making next epoch?",
         ["marinade_staking_report"], ["stake","validator","marinade"], False, False, True,
         "En büyük stake değişimi"),
    Case("stake_rpt_06", "marinade_staking_report", "staking_report",
         "Explain Marinade's delegation strategy based on the next epoch staking report.",
         ["marinade_staking_report","marinade_validator_scores"],
         ["stake","delegation","marinade","validator"], False, True, True,
         "Delegasyon stratejisi açıklama"),

    # ══════════════════════════════════════════════════════
    # 12. MARINADE_SCORING_REPORTS (5 case)
    # ══════════════════════════════════════════════════════
    Case("scoring_rpt_01", "marinade_scoring_reports", "scoring_reports",
         "Are there historical scoring reports from Marinade? List available reports.",
         ["marinade_scoring_reports"], ["report","marinade","scoring"], False, False, True,
         "Raporları listele"),
    Case("scoring_rpt_02", "marinade_scoring_reports", "scoring_reports",
         "How often does Marinade publish scoring reports? Show the report history.",
         ["marinade_scoring_reports"], ["report","marinade"], False, False, True,
         "Rapor sıklığı"),
    Case("scoring_rpt_03", "marinade_scoring_reports", "scoring_reports",
         "What are the latest Marinade scoring reports? When was the most recent one?",
         ["marinade_scoring_reports"], ["report","scoring","marinade"], False, False, True,
         "Son scoring raporları"),
    Case("scoring_rpt_04", "marinade_scoring_reports", "scoring_reports",
         "Marinade'ın geçmiş puanlama raporları var mı? Liste göster.",
         ["marinade_scoring_reports"], ["report","marinade"], False, False, True,
         "Türkçe rapor listesi"),
    Case("scoring_rpt_05", "marinade_scoring_reports", "scoring_reports",
         "Show Marinade scoring report history and explain what these reports contain.",
         ["marinade_scoring_reports"], ["report","scoring","marinade"], False, False, True,
         "Raporlar ne içeriyor?"),

    # ══════════════════════════════════════════════════════
    # 13. MARINADE_COMMISSION_CHANGES (6 case)
    # ══════════════════════════════════════════════════════
    Case("comm_chg_01", "marinade_commission_changes", "comm_changes",
         "Which Marinade validators have changed their commission recently?",
         ["marinade_commission_changes"], ["commission","validator","marinade"], False, True, True,
         "Son komisyon değişiklikleri"),
    Case("comm_chg_02", "marinade_commission_changes", "comm_changes",
         "Have any Marinade validators raised their commission? I want to know which ones increased.",
         ["marinade_commission_changes"], ["commission","increase","validator"], False, True, True,
         "Komisyon artışı tespiti"),
    Case("comm_chg_03", "marinade_commission_changes", "comm_changes",
         "Are there validators that cut their commission on Marinade recently?",
         ["marinade_commission_changes"], ["commission","validator"], False, True, True,
         "Komisyon düşürenleri bul"),
    Case("comm_chg_04", "marinade_commission_changes", "comm_changes",
         "Son zamanlarda komisyonunu artıran Marinade validatörleri var mı? Risk oluşturuyor mu?",
         ["marinade_commission_changes"], ["commission","validator","marinade"], False, True, True,
         "Türkçe komisyon risk"),
    Case("comm_chg_05", "marinade_commission_changes", "comm_changes",
         "Show me all recent commission changes in the Marinade network sorted by epoch.",
         ["marinade_commission_changes"], ["commission","epoch","validator"], False, True, True,
         "Epoch'a göre komisyon geçmişi"),
    Case("comm_chg_06", "marinade_commission_changes", "comm_changes",
         "Which validators on Marinade are most trustworthy based on commission stability?",
         ["marinade_commission_changes","marinade_validator_scores"],
         ["commission","validator","marinade"], False, True, True,
         "Güvenilir validatorlar (komisyon stabilite)"),

    # ══════════════════════════════════════════════════════
    # 14. MARINADE_CONFIG (5 case)
    # ══════════════════════════════════════════════════════
    Case("config_01", "marinade_config", "config",
         "What are the Marinade system configuration and program addresses?",
         ["marinade_config"], ["marinade","config"], False, False, True,
         "Sistem konfigürasyonu"),
    Case("config_02", "marinade_config", "config",
         "What scoring parameters does Marinade use in its delegation strategy?",
         ["marinade_config"], ["marinade","scoring","config"], False, True, True,
         "Scoring parametreleri"),
    Case("config_03", "marinade_config", "config",
         "What is the maximum commission a validator can have to be eligible for Marinade delegation?",
         ["marinade_config"], ["marinade","commission","config"], True, False, True,
         "Maksimum komisyon eşiği"),
    Case("config_04", "marinade_config", "config",
         "Marinade'ın delegation stratejisi nasıl konfigüre edilmiş? Parametreleri açıkla.",
         ["marinade_config"], ["marinade","config"], False, False, True,
         "Türkçe config açıklama"),
    Case("config_05", "marinade_config", "config",
         "Show Marinade configuration — specifically the scoring weights used for validator evaluation.",
         ["marinade_config","marinade_score_breakdown"], ["marinade","config","weight","score"], False, True, True,
         "Skor ağırlıkları config"),

    # ══════════════════════════════════════════════════════
    # 15. MARINADE_NATIVE_APY (6 case)
    # ══════════════════════════════════════════════════════
    Case("native_01", "marinade_native_apy", "native_apy",
         "What is Marinade's current native staking APY? Show the most recent epochs.",
         ["marinade_native_apy"], ["apy","marinade","epoch"], True, True, True,
         "Güncel native APY"),
    Case("native_02", "marinade_native_apy", "native_apy",
         "Is Marinade's native staking APY trending up or down over the past weeks?",
         ["marinade_native_apy"], ["apy","epoch","marinade"], True, True, True,
         "APY trend analizi"),
    Case("native_03", "marinade_native_apy", "native_apy",
         "Compare 5-epoch vs 10-epoch vs all-epoch native staking APY on Marinade.",
         ["marinade_native_apy"], ["apy","epoch","marinade"], True, True, True,
         "5/10/all epoch APY karşılaştırması"),
    Case("native_04", "marinade_native_apy", "native_apy",
         "Show me Marinade's native staking APY history for the last 20 epochs.",
         ["marinade_native_apy"], ["apy","epoch","marinade"], True, True, True,
         "20 epoch APY geçmişi"),
    Case("native_05", "marinade_native_apy", "native_apy",
         "Marinade native staking APY'si nedir? mSOL APY ile karşılaştır.",
         ["marinade_native_apy","marinade_msol_apy"], ["apy","marinade"], True, True, True,
         "Türkçe native vs mSOL"),
    Case("native_06", "marinade_native_apy", "native_apy",
         "What is the difference between Marinade native staking APY and mSOL liquid staking APY?",
         ["marinade_native_apy","marinade_msol_apy"], ["apy","native","msol"], True, True, True,
         "Native vs mSOL APY farkı"),

    # ══════════════════════════════════════════════════════
    # 16. MARINADE_JITO_COMMISSIONS (5 case)
    # ══════════════════════════════════════════════════════
    Case("jito_comm_01", "marinade_jito_commissions", "jito_comm",
         "Which Marinade validators have the lowest Jito MEV commission?",
         ["marinade_jito_commissions"], ["commission","jito","validator"], True, True, True,
         "En düşük Jito komisyonu"),
    Case("jito_comm_02", "marinade_jito_commissions", "jito_comm",
         "Show Marinade validators ranked by MEV commission — best for delegators first.",
         ["marinade_jito_commissions"], ["commission","jito","mev","validator"], True, True, True,
         "Delegatörler için en iyi"),
    Case("jito_comm_03", "marinade_jito_commissions", "jito_comm",
         "What is the average Jito MEV commission among Marinade validators?",
         ["marinade_jito_commissions"], ["commission","jito","mev","bps"], True, False, True,
         "Ortalama Jito MEV komisyonu"),
    Case("jito_comm_04", "marinade_jito_commissions", "jito_comm",
         "Marinade validatörlerinin Jito MEV komisyonları ne kadar? En iyi 10'unu göster.",
         ["marinade_jito_commissions"], ["commission","jito","validator"], True, True, True,
         "Türkçe Jito komisyon"),
    Case("jito_comm_05", "marinade_jito_commissions", "jito_comm",
         "Do Marinade validators pass most MEV rewards to stakers? Show their commission breakdown.",
         ["marinade_jito_commissions"], ["commission","mev","jito","validator"], True, True, True,
         "MEV paylaşım oranı"),

    # ══════════════════════════════════════════════════════
    # 17. MARINADE_MSOL_VOTES (7 case)
    # ══════════════════════════════════════════════════════
    Case("votes_msol_01", "marinade_msol_votes", "snapshot",
         "Which validators do mSOL holders vote for in Marinade governance?",
         ["marinade_msol_votes"], ["vote","validator","msol"], False, True, True,
         "mSOL oy dağılımı"),
    Case("votes_msol_02", "marinade_msol_votes", "snapshot",
         "Show me the mSOL governance vote distribution — which validator gets the most mSOL votes?",
         ["marinade_msol_votes"], ["vote","validator","msol"], False, True, True,
         "En çok oy alan validator"),
    Case("votes_msol_03", "marinade_msol_votes", "snapshot",
         "Is there vote concentration risk in Marinade mSOL governance? Who dominates?",
         ["marinade_msol_votes"], ["vote","validator","msol"], False, True, True,
         "Oy yoğunlaşma riski"),
    Case("votes_msol_04", "marinade_msol_votes", "snapshot",
         "How many mSOL holders are participating in Marinade governance voting?",
         ["marinade_msol_votes"], ["vote","voter","msol"], False, False, True,
         "Katılımcı sayısı"),
    Case("votes_msol_05", "marinade_msol_votes", "snapshot",
         "mSOL sahipleri Marinade yönetiminde hangi validatörleri destekliyor?",
         ["marinade_msol_votes"], ["vote","validator","msol"], False, True, True,
         "Türkçe mSOL oy dağılımı"),
    Case("votes_msol_06", "marinade_msol_votes", "snapshot",
         "Show the top 5 validators by mSOL vote weight in Marinade governance.",
         ["marinade_msol_votes"], ["vote","validator","msol"], False, True, False,
         "Top 5 by mSOL vote weight"),
    Case("votes_msol_07", "marinade_msol_votes", "snapshot",
         "Compare mSOL governance votes vs veMNDE votes — do the same validators dominate both?",
         ["marinade_msol_votes","marinade_vemnde_votes"], ["vote","validator","msol","vemnde"], False, True, True,
         "mSOL vs veMNDE oy karşılaştırması"),

    # ══════════════════════════════════════════════════════
    # 18. MARINADE_VEMNDE_VOTES (6 case)
    # ══════════════════════════════════════════════════════
    Case("votes_vemnde_01", "marinade_vemnde_votes", "snapshot",
         "Show the veMNDE governance vote distribution — which validators get veMNDE votes?",
         ["marinade_vemnde_votes"], ["vote","validator","vemnde"], False, True, True,
         "veMNDE oy dağılımı"),
    Case("votes_vemnde_02", "marinade_vemnde_votes", "snapshot",
         "Which validator has the most veMNDE vote weight in Marinade governance?",
         ["marinade_vemnde_votes"], ["vote","validator","vemnde"], False, False, True,
         "En fazla veMNDE oyu alan"),
    Case("votes_vemnde_03", "marinade_vemnde_votes", "snapshot",
         "Is veMNDE governance voting concentrated in a few validators or well distributed?",
         ["marinade_vemnde_votes"], ["vote","validator","vemnde"], False, True, True,
         "Oy yoğunlaşma analizi"),
    Case("votes_vemnde_04", "marinade_vemnde_votes", "snapshot",
         "What is veMNDE and how does it vote in Marinade governance? Show the current data.",
         ["marinade_vemnde_votes"], ["vote","vemnde","validator"], False, True, True,
         "veMNDE nedir + oy dağılımı"),
    Case("votes_vemnde_05", "marinade_vemnde_votes", "snapshot",
         "veMNDE sahiplerinin oy dağılımını göster. Hangi validatorlar öne çıkıyor?",
         ["marinade_vemnde_votes"], ["vemnde","validator","vote"], False, True, True,
         "Türkçe veMNDE oyu"),
    Case("votes_vemnde_06", "marinade_vemnde_votes", "snapshot",
         "How many total veMNDE voters are there and what is the top validator's share?",
         ["marinade_vemnde_votes"], ["vemnde","voter","vote"], False, False, True,
         "Toplam katılımcı ve lider payı"),

    # ══════════════════════════════════════════════════════
    # 19. MARINADE_WALLET_MSOL_BALANCE (5 case)
    # ══════════════════════════════════════════════════════
    Case("wallet_msol_01", "marinade_wallet_msol_balance", "snapshot",
         f"What is the mSOL balance for wallet {SNAPSHOT_WALLET}?",
         ["marinade_wallet_msol_balance"], ["msol","wallet","amount"], False, False, True,
         "Wallet mSOL bakiyesi"),
    Case("wallet_msol_02", "marinade_wallet_msol_balance", "snapshot",
         f"How much mSOL does address {SNAPSHOT_WALLET} hold according to the latest Marinade snapshot?",
         ["marinade_wallet_msol_balance"], ["msol","amount","snapshot"], False, False, True,
         "Snapshot'tan mSOL miktarı"),
    Case("wallet_msol_03", "marinade_wallet_msol_balance", "snapshot",
         f"{SNAPSHOT_WALLET} adresindeki mSOL bakiyesini göster. Ne zaman kaydedilmiş?",
         ["marinade_wallet_msol_balance"], ["msol","amount"], False, False, True,
         "Türkçe wallet mSOL bakiyesi"),
    Case("wallet_msol_04", "marinade_wallet_msol_balance", "snapshot",
         f"Check mSOL snapshot balance for {SNAPSHOT_WALLET}. When was this snapshot taken?",
         ["marinade_wallet_msol_balance"], ["msol","amount","snapshot","slot"], False, False, True,
         "Snapshot zamanı ile birlikte"),
    Case("wallet_msol_05", "marinade_wallet_msol_balance", "snapshot",
         "What mSOL does wallet 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM hold in Marinade?",
         ["marinade_wallet_msol_balance"], ["msol","wallet"], False, False, False,
         "Farklı wallet — büyük ihtimal 0"),

    # ══════════════════════════════════════════════════════
    # 20. MARINADE_WALLET_VEMNDE_BALANCE (5 case)
    # ══════════════════════════════════════════════════════
    Case("wallet_vemnde_01", "marinade_wallet_vemnde_balance", "snapshot",
         f"What is the veMNDE balance for wallet {SNAPSHOT_WALLET}?",
         ["marinade_wallet_vemnde_balance"], ["vemnde","wallet"], False, False, False,
         "Wallet veMNDE bakiyesi"),
    Case("wallet_vemnde_02", "marinade_wallet_vemnde_balance", "snapshot",
         f"Does {SNAPSHOT_WALLET} hold any veMNDE tokens according to Marinade snapshots?",
         ["marinade_wallet_vemnde_balance"], ["vemnde","wallet"], False, False, True,
         "veMNDE varlık kontrolü"),
    Case("wallet_vemnde_03", "marinade_wallet_vemnde_balance", "snapshot",
         f"Check veMNDE governance token balance for address {SNAPSHOT_WALLET}.",
         ["marinade_wallet_vemnde_balance"], ["vemnde","wallet"], False, False, True,
         "veMNDE governance token kontrolü"),
    Case("wallet_vemnde_04", "marinade_wallet_vemnde_balance", "snapshot",
         "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM cüzdanındaki veMNDE miktarını göster.",
         ["marinade_wallet_vemnde_balance"], ["vemnde"], False, False, False,
         "Türkçe veMNDE bakiyesi"),
    Case("wallet_vemnde_05", "marinade_wallet_vemnde_balance", "snapshot",
         "Does address 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM have veMNDE tokens?",
         ["marinade_wallet_vemnde_balance"], ["vemnde"], False, False, False,
         "Bilinmeyen wallet veMNDE"),

    # ══════════════════════════════════════════════════════
    # 21. MARINADE_WALLET_NATIVE_STAKE (5 case)
    # ══════════════════════════════════════════════════════
    Case("native_stake_01", "marinade_wallet_native_stake", "snapshot",
         f"Show native stake history for wallet {SNAPSHOT_WALLET} from 2026-04-13 to 2026-04-20.",
         ["marinade_wallet_native_stake"], ["stake","wallet","native"], False, False, True,
         "Native stake geçmişi"),
    Case("native_stake_02", "marinade_wallet_native_stake", "snapshot",
         f"How much SOL was natively staked by {SNAPSHOT_WALLET} over the past week?",
         ["marinade_wallet_native_stake"], ["stake","sol","wallet"], False, False, True,
         "Son 1 hafta native stake"),
    Case("native_stake_03", "marinade_wallet_native_stake", "snapshot",
         "Show me the Marinade native stake history for wallet 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM between April 13 and April 20, 2026.",
         ["marinade_wallet_native_stake"], ["stake","wallet","native"], False, False, True,
         "Tarih aralığı native stake"),
    Case("native_stake_04", "marinade_wallet_native_stake", "snapshot",
         f"{SNAPSHOT_WALLET} adresinin native stake geçmişini 2026-04-13 ile 2026-04-20 arasında göster.",
         ["marinade_wallet_native_stake"], ["stake","wallet"], False, False, True,
         "Türkçe native stake geçmişi"),
    Case("native_stake_05", "marinade_wallet_native_stake", "snapshot",
         f"Has {SNAPSHOT_WALLET} changed its native staking amount recently? Check last 7 days.",
         ["marinade_wallet_native_stake"], ["stake","wallet","native"], False, False, True,
         "Native stake değişim kontrolü"),
]


# ─── Çalıştırıcı ─────────────────────────────────────────────────────────────

async def run_one(case: Case, qfn) -> Result:
    t0 = time.time()
    for attempt in range(3):
        try:
            r = await qfn(case.query)
            q = quality(r["html"])
            return Result(case, r["tools_called"], r["html"], r["plain"], time.time()-t0, q)
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                w = 20 + attempt * 15
                print(f"   [429 — {w}s bekleniyor]")
                await asyncio.sleep(w)
            else:
                return Result(case, [], "", "", time.time()-t0, {}, str(e)[:200])


def show(r: Result, print_html: bool = False):
    ok = "✅" if r.passed else "❌"
    print(f"\n{'─'*70}")
    print(f"{ok} [{r.case.id}] {r.case.desc}")
    print(f"   Tool     : {r.case.tool} (group:{r.case.group})")
    print(f"   Query    : {r.case.query[:90]}{'...' if len(r.case.query)>90 else ''}")
    print(f"   Tools    : {r.tools}")
    print(f"   Süre/HTML: {r.dur:.1f}s / {len(r.html):,} chars / kalite:{r.score}/100")

    if r.err:
        print(f"   HATA     : {r.err[:180]}")
        return

    q = r.q
    flags = []
    if q.get("wrapper"):  flags.append("🏗")
    if q.get("insight"):  flags.append("💡")
    if q.get("warning"):  flags.append("⚠")
    if q.get("table"):    flags.append("📊")
    if q.get("cards"):    flags.append("🃏")
    if q.get("percent"):  flags.append("📈%")
    if q.get("not_raw"):  flags.append("✍")
    print(f"   HTML     : {' '.join(flags) or 'ZAYIF'}")

    if r.case.kw:
        hits  = [k for k,v in kw_check(r.html, *r.case.kw).items() if v]
        misses= [k for k,v in kw_check(r.html, *r.case.kw).items() if not v]
        if hits:   print(f"   ✓ kw     : {hits}")
        if misses: print(f"   ✗ eksik  : {misses}")

    musts = []
    if r.case.need_pct:
        musts.append(f"{'✅' if q.get('percent') else '❌'}%")
    if r.case.need_table:
        musts.append(f"{'✅' if q.get('table') else '❌'}table")
    if r.case.need_insight:
        musts.append(f"{'✅' if q.get('insight') else '❌'}insight")
    if musts:
        print(f"   Zorunlu  : {' | '.join(musts)}")

    plain = re.sub(r"\s+", " ", r.plain[:280]).strip()
    print(f"   Yanıt    : {plain}...")

    if print_html:
        print(f"\n{'▼'*70}\n{r.html}\n{'▲'*70}")


async def run_all(cases: list[Case], print_html: bool = False):
    from orchestrator import query as qfn

    print(f"\n{'═'*70}")
    print(f"Marinade Full Coverage LLM Test Suite")
    print(f"{'═'*70}")
    print(f"Toplam: {len(cases)} test")

    results: list[Result] = []
    for i, c in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {c.id} ...", flush=True)
        r = await run_one(c, qfn)
        results.append(r)
        show(r, print_html)
        if i < len(cases):
            await asyncio.sleep(2)

    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    errors = sum(1 for r in results if r.err)
    avg_q  = sum(r.score for r in results) / total if total else 0
    avg_d  = sum(r.dur for r in results) / total if total else 0
    avg_l  = sum(len(r.html) for r in results) / total if total else 0

    pct_req = [r for r in results if r.case.need_pct and not r.err]
    tbl_req = [r for r in results if r.case.need_table and not r.err]
    ins_req = [r for r in results if r.case.need_insight and not r.err]

    print(f"\n{'═'*70}")
    print(f"ÖZET")
    print(f"{'═'*70}")
    print(f"  ✅ Passed     : {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"  ❌ Hata       : {errors}")
    print(f"  📊 Ort kalite : {avg_q:.0f}/100")
    print(f"  ⏱ Avg time  : {avg_d:.1f}s")
    print(f"  📄 Ort HTML   : {avg_l:,.0f} chars")
    if pct_req:
        ok = sum(1 for r in pct_req if r.q.get("percent"))
        print(f"  📈 % gösterim : {ok}/{len(pct_req)} ({ok/len(pct_req)*100:.0f}%)")
    if tbl_req:
        ok = sum(1 for r in tbl_req if r.q.get("table"))
        print(f"  📊 Tablo      : {ok}/{len(tbl_req)} ({ok/len(tbl_req)*100:.0f}%)")
    if ins_req:
        ok = sum(1 for r in ins_req if r.q.get("insight"))
        print(f"  💡 Insight    : {ok}/{len(ins_req)} ({ok/len(ins_req)*100:.0f}%)")

    print(f"\n{'─'*70}")
    print("BAŞARISIZLAR:")
    failed = [r for r in results if not r.passed]
    if not failed:
        print("  All passed! 🎉")
    else:
        for r in failed:
            msg = r.err or f"beklenen:{r.case.expected} gelen:{r.tools}"
            print(f"  ✗ {r.case.id:<25} {msg[:100]}")

    print(f"\n{'─'*70}")
    print("TOOL BAZLI:")
    by_tool: dict[str, list[Result]] = defaultdict(list)
    for r in results: by_tool[r.case.tool].append(r)
    for t, tr in sorted(by_tool.items()):
        ok  = sum(1 for r in tr if r.passed)
        aq  = sum(r.score for r in tr) / len(tr)
        bar = "█"*ok + "░"*(len(tr)-ok)
        print(f"  {t:<40}: [{bar}] {ok}/{len(tr)} kalite:{aq:.0f}")

    print(f"\n{'─'*70}")
    print("KALİTE SIRALAMASI (en kötü 10):")
    worst = sorted(results, key=lambda r: r.score)[:10]
    for r in worst:
        ok = "✅" if r.passed else "❌"
        print(f"  {r.score:3d}/100 {ok} {r.case.id}")

    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tool",       help="Belirli tool filtrele (ör: marinade_stats)")
    p.add_argument("--id",         help="Belirli test ID")
    p.add_argument("--group",      help="Grup filtrele (stats/apy/validators/scores/breakdown/uptimes/comm_hist/versions/cluster/rewards/staking_report/scoring_reports/comm_changes/config/native_apy/jito_comm/snapshot)")
    p.add_argument("--print-html", action="store_true")
    args = p.parse_args()

    cases = CASES
    if args.id:
        cases = [c for c in cases if c.id == args.id]
    elif args.tool:
        cases = [c for c in cases if c.tool == args.tool]
    elif args.group:
        cases = [c for c in cases if c.group == args.group]

    asyncio.run(run_all(cases, args.print_html))

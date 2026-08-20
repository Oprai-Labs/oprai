"""
Kamino Finance LLM End-to-End Test Suite
─────────────────────────────────────────
Tüm Kamino GET tool'larını tüm parametre kombinasyonları ve uç case'lerle LLM üzerinden test eder.

Çalıştır:
  cd services/defi-query-service
  OPRAI_OPENAI_API_KEY=sk-... python3 tests/e2e_llm_kamino.py
  OPRAI_OPENAI_API_KEY=sk-... python3 tests/e2e_llm_kamino.py --tool kamino_market_reserves
  OPRAI_OPENAI_API_KEY=sk-... python3 tests/e2e_llm_kamino.py --id res_01_best_supply_apy
  OPRAI_OPENAI_API_KEY=sk-... python3 tests/e2e_llm_kamino.py --fast
"""

import asyncio
import re
import sys
import time
import os
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── Bilinen cüzdan adresleri (test için) ────────────────────────────────────

EMPTY_WALLET = "GJGkJEFPDdFwPsnCyJqoYJGZTHyv9NM6qwqJvLSHFCH"   # Boş / küçük cüzdan
FAKE_WALLET  = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"    # Geçersiz adres

# LST mint adresleri
JITOSOL  = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
MSOL     = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
BSOL     = "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1"
SOL      = "So11111111111111111111111111111111111111112"
USDC     = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ─── Analiz yardımcıları ─────────────────────────────────────────────────────

def html_has(html: str, *keywords: str) -> dict[str, bool]:
    plain = re.sub(r"<[^>]+>", "", html).lower()
    return {kw: kw.lower() in plain for kw in keywords}

def check_has_percentage(html: str) -> bool:
    return bool(re.search(r"[↑↓%][^\w]?\s*[\d.]+|[\d.]+\s*%", html))

def check_html_structure(html: str) -> dict:
    return {
        "has_wrapper":      "font-family" in html,
        "has_insight_box":  "💡" in html or "insight" in html.lower(),
        "has_warning_box":  "⚠" in html or "warning" in html.lower() or "risk" in html.lower(),
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
    # KAMINO_MARKET_RESERVES — 55 reserve, borrowApy/supplyApy/TVL/maxLtv
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="res_01_best_supply_apy",
        tool="kamino_market_reserves",
        query="Kamino lending piyasasında en yüksek supply APY veren tokenlar hangileri?",
        expected_tools=["kamino_market_reserves"],
        check_html=["apy", "kamino", "%"],
        check_fields=["supplyApy", "liquidityToken"],
        description="En yüksek supply APY sıralaması",
    ),
    TestCase(
        id="res_02_best_borrow_rate",
        tool="kamino_market_reserves",
        query="Kamino'da en ucuza hangi tokeni borç alabilirim? Borrow APY sıralaması yap",
        expected_tools=["kamino_market_reserves"],
        check_html=["borrow", "apy", "%"],
        check_fields=["borrowApy"],
        description="En düşük borrow APY sıralaması",
    ),
    TestCase(
        id="res_03_sol_reserve",
        tool="kamino_market_reserves",
        query="Kamino'da SOL rezervinin supply ve borrow APY'si nedir? Şu an ne kadar SOL yatırılmış?",
        expected_tools=["kamino_market_reserves"],
        check_html=["sol", "apy", "%"],
        check_fields=["supplyApy", "borrowApy", "totalSupply"],
        description="SOL reserve detay sorgusu",
    ),
    TestCase(
        id="res_04_usdc_reserve",
        tool="kamino_market_reserves",
        query="Kamino'da USDC lend edersem yıllık ne kadar kazanırım?",
        expected_tools=["kamino_market_reserves"],
        check_html=["usdc", "%", "kamino"],
        check_fields=["supplyApy"],
        description="USDC lending getiri hesabı",
    ),
    TestCase(
        id="res_05_high_utilization",
        tool="kamino_market_reserves",
        query="Kamino'da hangi rezervlerin kullanım oranı en yüksek? Ödünç alım/arz oranını göster",
        expected_tools=["kamino_market_reserves"],
        check_html=["kullanım", "oran", "kamino"],
        check_fields=["totalBorrow", "totalSupply"],
        description="Utilization oranı analizi",
    ),
    TestCase(
        id="res_06_tvl_ranking",
        tool="kamino_market_reserves",
        query="Kamino lending'deki en büyük rezervler hangileri? TVL'e göre sırala",
        expected_tools=["kamino_market_reserves"],
        check_html=["tvl", "kamino", "$"],
        check_fields=["totalSupplyUsd"],
        description="TVL bazlı reserve sıralaması",
    ),
    TestCase(
        id="res_07_max_ltv_analysis",
        tool="kamino_market_reserves",
        query="Kamino'da teminat olarak yatırdığım SOL ile ne kadar borç alabilirim? Max LTV nedir?",
        expected_tools=["kamino_market_reserves"],
        check_html=["ltv", "teminat", "sol"],
        check_fields=["maxLtv"],
        description="Max LTV teminat analizi",
    ),
    TestCase(
        id="res_08_risk_high_borrow",
        tool="kamino_market_reserves",
        query="Kamino'da borrow APY'si yüksek olan riskli rezervler var mı?",
        expected_tools=["kamino_market_reserves"],
        check_html=["borrow", "risk", "%"],
        check_fields=["borrowApy"],
        must_warn=True,
        description="Yüksek borrow APY riski uyarısı",
    ),
    TestCase(
        id="res_09_stablecoin_comparison",
        tool="kamino_market_reserves",
        query="Kamino'da USDC, USDT ve USDS'in lending getirilerini karşılaştır",
        expected_tools=["kamino_market_reserves"],
        check_html=["usdc", "usdt", "stablecoin"],
        check_fields=["supplyApy", "borrowApy"],
        description="Stablecoin lending karşılaştırması",
    ),
    TestCase(
        id="res_10_lst_borrow_vs_native",
        tool="kamino_market_reserves",
        query="Kamino'da JitoSOL ve mSOL'u teminat olarak kullananlar ne kadar LTV alabilir?",
        expected_tools=["kamino_market_reserves"],
        check_html=["jitosol", "msol", "ltv"],
        check_fields=["maxLtv", "liquidityToken"],
        description="LST teminat LTV karşılaştırması",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_STAKING_YIELDS — 41 LST, tokenMint, apy
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="yield_01_all_lst",
        tool="kamino_staking_yields",
        query="Kamino'daki tüm LST token staking getirilerini göster",
        expected_tools=["kamino_staking_yields"],
        check_html=["lst", "apy", "%"],
        check_fields=["apy", "tokenMint"],
        description="Tüm LST staking yield listesi",
    ),
    TestCase(
        id="yield_02_highest_apy",
        tool="kamino_staking_yields",
        query="Kamino'da en yüksek staking APY'si hangi LST token'da?",
        expected_tools=["kamino_staking_yields"],
        check_html=["apy", "%"],
        check_fields=["apy"],
        description="En yüksek LST APY sorgusu",
    ),
    TestCase(
        id="yield_03_jitosol_vs_msol",
        tool="kamino_staking_yields",
        query="JitoSOL mu daha iyi staking getirisi veriyor yoksa mSOL mu? Kamino verisiyle karşılaştır",
        expected_tools=["kamino_staking_yields"],
        check_html=["jitosol", "msol", "apy"],
        check_fields=["apy"],
        description="JitoSOL vs mSOL APY karşılaştırması",
    ),
    TestCase(
        id="yield_04_mean_yield",
        tool="kamino_staking_yields_mean",
        query="Kamino'daki LST tokenların ortalama staking getirisi nedir?",
        expected_tools=["kamino_staking_yields_mean", "kamino_staking_yields"],
        check_html=["ortalama", "apy", "%"],
        check_fields=["apy"],
        description="Ortalama LST staking yield",
    ),
    TestCase(
        id="yield_05_vs_direct_stake",
        tool="kamino_staking_yields",
        query="Kamino LST getirisi ile doğrudan Solana validator stake karşılaştırması — hangisi mantıklı?",
        expected_tools=["kamino_staking_yields"],
        check_html=["lst", "staking", "apy"],
        check_fields=["apy"],
        description="LST vs doğrudan stake kıyası",
    ),
    TestCase(
        id="yield_06_bsol_apy",
        tool="kamino_staking_yields",
        query=f"bSOL (mint: {BSOL}) staking APY'si ne kadar?",
        expected_tools=["kamino_staking_yields", "kamino_staking_yields_mean"],
        check_html=["bsol", "apy"],
        check_fields=["apy"],
        description="bSOL spesifik APY sorgusu",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_LEVERAGE_STATS — tvl, avgLeverage, totalBorrowedUsd
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="lev_01_avg_leverage",
        tool="kamino_leverage_stats",
        query="Kamino'da leveraged pozisyon kullananların ortalama kaldıraç oranı nedir?",
        expected_tools=["kamino_leverage_stats"],
        check_html=["kaldıraç", "oran", "kamino"],
        check_fields=["avgLeverage"],
        description="Ortalama leverage oranı",
    ),
    TestCase(
        id="lev_02_total_tvl",
        tool="kamino_leverage_stats",
        query="Kamino leverage'da ne kadar TVL var? Toplam kaldıraçlı pozisyon değeri nedir?",
        expected_tools=["kamino_leverage_stats"],
        check_html=["tvl", "$", "kamino"],
        check_fields=["tvl", "totalBorrowedUsd"],
        description="Leverage toplam TVL analizi",
    ),
    TestCase(
        id="lev_03_high_leverage_pairs",
        tool="kamino_leverage_stats",
        query="Kamino'da hangi reserve çifti en yüksek ortalama kaldıraçla kullanılıyor?",
        expected_tools=["kamino_leverage_stats"],
        check_html=["kaldıraç", "çift"],
        check_fields=["avgLeverage"],
        description="En yüksek leverage reserve çifti",
    ),
    TestCase(
        id="lev_04_risk_assessment",
        tool="kamino_leverage_stats",
        query="Kamino leveraged pozisyonları ne kadar riskli? Toplam borç vs deposit oranı nedir?",
        expected_tools=["kamino_leverage_stats"],
        check_html=["risk", "borç", "kamino"],
        check_fields=["totalBorrowedUsd", "totalDepositedUsd"],
        must_warn=True,
        description="Leverage risk değerlendirmesi",
    ),
    TestCase(
        id="lev_05_obligation_count",
        tool="kamino_leverage_stats",
        query="Kamino'da kaç tane aktif leveraged pozisyon var?",
        expected_tools=["kamino_leverage_stats"],
        check_html=["pozisyon", "sayı", "kamino"],
        check_fields=["totalObligations"],
        description="Aktif leverage pozisyon sayısı",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_STRATEGIES — LP vault strategies
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="strat_01_top_apy",
        tool="kamino_strategies",
        query="Kamino'daki en yüksek APY'li likidite vault stratejileri hangileri?",
        expected_tools=["kamino_strategies"],
        check_html=["apy", "kamino", "%"],
        check_fields=["apy"],
        description="En yüksek APY'li LP stratejileri",
    ),
    TestCase(
        id="strat_02_sol_usdc",
        tool="kamino_strategies",
        query="Kamino'da SOL/USDC çifti için LP vault stratejileri var mı?",
        expected_tools=["kamino_strategies"],
        check_html=["sol", "usdc", "kamino"],
        check_fields=["tokenA", "tokenB"],
        description="SOL/USDC LP vault araması",
    ),
    TestCase(
        id="strat_03_highest_tvl",
        tool="kamino_strategies",
        query="Kamino vault'larında en fazla TVL olan strateji hangisi?",
        expected_tools=["kamino_strategies"],
        check_html=["tvl", "$", "kamino"],
        check_fields=["totalValueLocked"],
        description="En yüksek TVL vault stratejisi",
    ),
    TestCase(
        id="strat_04_30d_apy",
        tool="kamino_strategies",
        query="Kamino vault'larının 30 günlük ortalama APY performansını göster",
        expected_tools=["kamino_strategies"],
        check_html=["30", "apy", "%"],
        check_fields=["apy"],
        description="30 günlük APY performansı",
    ),
    TestCase(
        id="strat_05_fee_analysis",
        tool="kamino_strategies",
        query="Kamino LP stratejilerinin fee APR'ı nedir? Sadece işlem ücretlerinden gelen getiri",
        expected_tools=["kamino_strategies"],
        check_html=["fee", "apr", "kamino"],
        check_fields=["feeApr"],
        description="Fee APR analizi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_ORACLE_PRICES — token prices from Kamino oracle
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="oracle_01_sol_price",
        tool="kamino_oracle_prices",
        query="Kamino oracle'ının SOL için kullandığı fiyat nedir?",
        expected_tools=["kamino_oracle_prices"],
        check_html=["sol", "$", "oracle"],
        check_fields=["price"],
        description="SOL oracle fiyatı",
    ),
    TestCase(
        id="oracle_02_usdc_peg",
        tool="kamino_oracle_prices",
        query="Kamino oracle'ında USDC ve USDT fiyatları $1 peg'inde mi?",
        expected_tools=["kamino_oracle_prices"],
        check_html=["usdc", "usdt", "$1"],
        check_fields=["price"],
        description="Stablecoin peg kontrolü (oracle)",
    ),
    TestCase(
        id="oracle_03_lst_prices",
        tool="kamino_oracle_prices",
        query="Kamino'nun JitoSOL ve mSOL için oracle fiyatları nedir?",
        expected_tools=["kamino_oracle_prices"],
        check_html=["jitosol", "msol", "$"],
        check_fields=["price"],
        description="LST oracle fiyatları",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_MARKETS — 29 lending markets
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="mkt_01_list_all",
        tool="kamino_markets",
        query="Kamino'daki tüm lending market'leri listele",
        expected_tools=["kamino_markets"],
        check_html=["market", "kamino"],
        check_fields=["name", "isPrimary"],
        description="Tüm Kamino market listesi",
    ),
    TestCase(
        id="mkt_02_primary_market",
        tool="kamino_markets",
        query="Kamino'nun birincil (primary) lending market'inin adresi nedir?",
        expected_tools=["kamino_markets", "kamino_lending"],
        check_html=["primary", "market", "kamino"],
        check_fields=["isPrimary", "lendingMarket"],
        description="Primary market adresi sorgusu",
    ),
    TestCase(
        id="mkt_03_alt_markets",
        tool="kamino_markets",
        query="Kamino'da ana market dışında başka lending market'ler var mı?",
        expected_tools=["kamino_markets"],
        check_html=["market", "kamino"],
        check_fields=["name"],
        description="Alternatif market keşfi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_EARN_VAULTS — 121 earn vaults
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="earn_01_list",
        tool="kamino_earn_vaults",
        query="Kamino Earn vault'larını listele",
        expected_tools=["kamino_earn_vaults"],
        check_html=["vault", "kamino"],
        check_fields=["address"],
        description="Kamino Earn vault listesi",
    ),
    TestCase(
        id="earn_02_fees",
        tool="kamino_earn_vaults",
        query="Kamino Earn vault'larının fee yapısı nasıl? Management ve performance fee'ler nedir?",
        expected_tools=["kamino_earn_vaults"],
        check_html=["fee", "kamino", "vault"],
        check_fields=["performanceFeeBps", "managementFeeBps"],
        description="Vault fee yapısı analizi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_USER_POSITIONS & KAMINO_USER_OBLIGATIONS — wallet sorguları
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="user_01_no_positions",
        tool="kamino_user_positions",
        query=f"Bu cüzdanın Kamino Earn'deki pozisyonları neler: {EMPTY_WALLET}",
        expected_tools=["kamino_user_positions"],
        check_html=["kamino", "vault"],
        check_fields=["vaultAddress"],
        description="Boş cüzdan — earn pozisyonu yok",
    ),
    TestCase(
        id="user_02_no_loans",
        tool="kamino_user_obligations",
        query=f"Bu cüzdanın Kamino'daki borç pozisyonları ve health factor'ı nedir: {EMPTY_WALLET}",
        expected_tools=["kamino_user_obligations"],
        check_html=["kamino", "borç"],
        check_fields=["healthFactor"],
        description="Boş cüzdan — borç pozisyonu yok",
    ),
    TestCase(
        id="user_03_invalid_wallet",
        tool="kamino_user_positions",
        query=f"Bu cüzdanın Kamino pozisyonlarını getir: {FAKE_WALLET}",
        expected_tools=["kamino_user_positions", "kamino_user_obligations"],
        check_html=["kamino"],
        description="Geçersiz cüzdan adresi",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # KAMINO_EPOCH_INFO
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="epoch_01_current",
        tool="kamino_epoch_info",
        query="Solana şu an kaçıncı epoch'ta? Ne zaman başladı ne zaman bitiyor?",
        expected_tools=["kamino_epoch_info"],
        check_html=["epoch", "solana"],
        check_fields=["epoch", "startBlockTime"],
        description="Güncel epoch bilgisi",
    ),
    TestCase(
        id="epoch_02_staking_context",
        tool="kamino_epoch_info",
        query="Solana staking reward'ları ne zaman dağıtılacak? Epoch ne zaman bitiyor?",
        expected_tools=["kamino_epoch_info"],
        check_html=["epoch", "staking"],
        check_fields=["epoch"],
        description="Epoch bitiş zamanı / staking context",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # EDGE CASE'LER — multi-tool zincirleme ve karmaşık sorgular
    # ═══════════════════════════════════════════════════════════════════

    TestCase(
        id="edge_01_best_yield_strategy",
        tool="kamino_market_reserves",
        query=(
            "Elinde 1000 USDC var. Kamino'da bu USDC ile yapabileceğin en iyi getiri stratejisi nedir? "
            "Lend mi et, vault'a mı koy, yoksa leverage mı kullan? APY'leri karşılaştır"
        ),
        expected_tools=["kamino_market_reserves", "kamino_strategies"],
        check_html=["usdc", "apy", "kamino"],
        check_fields=["supplyApy"],
        description="En iyi USDC getiri stratejisi — multi-tool",
    ),
    TestCase(
        id="edge_02_lst_full_strategy",
        tool="kamino_staking_yields",
        query=(
            "JitoSOL tutuyorum. Kamino'da JitoSOL ile ne yapabilirim? "
            "Hem stake getirisi hem lending teminatı hem de APY karşılaştırması istiyorum"
        ),
        expected_tools=["kamino_staking_yields", "kamino_market_reserves"],
        check_html=["jitosol", "apy", "teminat"],
        check_fields=["apy"],
        description="JitoSOL tam strateji analizi",
    ),
    TestCase(
        tool="kamino_market_reserves",
        query="USDC lend etmek için Kamino mı yoksa Jupiter Lend mi daha iyi getiri veriyor?",
        expected_tools=["kamino_market_reserves"],
        check_html=["kamino", "jupiter", "usdc"],
        check_fields=["supplyApy"],
        description="Kamino vs Jupiter Lend lending karşılaştırması",
    ),
    TestCase(
        id="edge_04_leverage_risk_full",
        tool="kamino_leverage_stats",
        query=(
            "Kamino'da kaldıraçlı pozisyon açmak ne kadar riskli? "
            "Mevcut leverage istatistikleri, borrow APY'leri ve likidite riski hakkında tam analiz yap"
        ),
        expected_tools=["kamino_leverage_stats", "kamino_market_reserves"],
        check_html=["kaldıraç", "risk", "likidite"],
        check_fields=["avgLeverage", "borrowApy"],
        must_warn=True,
        description="Tam leverage risk analizi — multi-tool",
    ),
    TestCase(
        id="edge_05_protocol_overview",
        tool="kamino_market_reserves",
        query="Kamino Finance'in genel durumu nedir? Toplam TVL, kaç market var, lending ve vault APY'leri nasıl?",
        expected_tools=["kamino_market_reserves", "kamino_markets", "kamino_strategies"],
        check_html=["kamino", "tvl", "apy"],
        check_fields=["totalSupplyUsd"],
        description="Kamino protokol genel durum özeti",
    ),
    TestCase(
        id="edge_06_borrow_to_buy_sol",
        tool="kamino_market_reserves",
        query=(
            "USDC teminat verip SOL borç almak istiyorum. Kamino'da ne kadar borrow APY öderim? "
            "SOL borrow cost vs SOL staking yield karşılaştırması yap"
        ),
        expected_tools=["kamino_market_reserves", "kamino_staking_yields"],
        check_html=["borrow", "sol", "apy"],
        check_fields=["borrowApy"],
        description="USDC teminat → SOL borç stratejisi",
    ),
    TestCase(
        id="edge_07_lst_yield_vs_lending",
        tool="kamino_staking_yields",
        query="Kamino'da JitoSOL staking APY'si ile JitoSOL'u lend edersem kazanacağım APY'yi karşılaştır",
        expected_tools=["kamino_staking_yields", "kamino_market_reserves"],
        check_html=["jitosol", "staking", "lending"],
        check_fields=["apy", "supplyApy"],
        description="LST staking vs lending APY kıyası",
    ),
    TestCase(
        id="edge_08_complete_wallet_audit",
        tool="kamino_user_obligations",
        query=f"Bu cüzdanın tüm Kamino aktivitesini kontrol et: {EMPTY_WALLET} — earn pozisyonları, borçlar ve sağlık faktörü",
        expected_tools=["kamino_user_obligations", "kamino_user_positions"],
        check_html=["kamino", "cüzdan"],
        description="Tam cüzdan Kamino audit",
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
    print(f"   Sorgu    : {r.case.query[:90]}{'...' if len(r.case.query)>90 else ''}")
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
            print(f"   Risk uyar: {'✅ VAR' if has_warn else '❌ EKSİK'}")

    # İlk 200 karakter plain text
    plain_preview = re.sub(r"\s+", " ", r.plain[:200]).strip()
    print(f"   Response : {plain_preview}")


async def run_all(filter_val: Optional[str] = None, fast: bool = False):
    from orchestrator import query as _query

    cases = ALL_CASES
    if filter_val:
        cases = [c for c in cases if c.tool == filter_val or c.id == filter_val]

    print(f"\n{'═'*70}")
    print(f"Kamino Finance LLM End-to-End Test Suite")
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
        # Rate limit önlemi — ardışık çağrılar arasında kısa bekleme
        if i < len(cases):
            await asyncio.sleep(2)

    # ─── Özet raporu ──────────────────────────────────────────────────
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
            print(f"  ✗ {r.case.id}: {reason[:120]}")

    print(f"\n{'─'*70}")
    print("PER-TOOL SUMMARY:")
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
    parser.add_argument("--tool", help="Sadece belirli bir tool (ör: kamino_market_reserves)")
    parser.add_argument("--id",   help="Belirli bir test ID'si")
    parser.add_argument("--fast", action="store_true", help="Short output")
    args = parser.parse_args()

    asyncio.run(run_all(args.id or args.tool, args.fast))

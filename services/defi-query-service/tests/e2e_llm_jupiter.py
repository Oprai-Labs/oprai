"""
LLM End-to-End Jupiter Tool Tests
──────────────────────────────────
Exercises every Jupiter GET tool through the LLM, across every parameter
combination — including edge cases, error paths and response-quality analysis.

The `query` fields are deliberately written in Turkish: this suite doubles as
the regression net for Turkish-language intent handling.

Run:
  cd services/defi-query-service
  OPRAI_OPENAI_API_KEY=sk-... python3 tests/e2e_llm_jupiter.py
  OPRAI_OPENAI_API_KEY=sk-... python3 tests/e2e_llm_jupiter.py --tool jup_trending
  OPRAI_OPENAI_API_KEY=sk-... python3 tests/e2e_llm_jupiter.py --fast   (summary report)
"""

import asyncio
import re
import sys
import time
import os
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── Mint adresleri ──────────────────────────────────────────────────────────

SOL      = "So11111111111111111111111111111111111111112"
USDC     = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT     = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
BONK     = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
JUP      = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
WIF      = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
RAY      = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
PYTH     = "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"
JITOSOL  = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
MSOL     = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
FARTCOIN = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
POPCAT   = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
BOME     = "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82"
TRUMP    = "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN"
FAKE     = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # invalid mint


# ─── Response-analysis helpers ───────────────────────────────────────────────

def html_has(html: str, *keywords: str) -> dict[str, bool]:
    """Keyword checks against the rendered HTML."""
    plain = re.sub(r"<[^>]+>", "", html).lower()
    return {kw: kw.lower() in plain for kw in keywords}


def check_price_format(html: str) -> bool:
    """Is there a $X.XX / $X.XXXX formatted price?"""
    return bool(re.search(r"\$[\d,]+\.[\d]+", html))


def check_has_percentage(html: str) -> bool:
    """Is there a percentage value (↑/↓ or %)?"""
    return bool(re.search(r"[↑↓%][^\w]?\s*[\d.]+|[\d.]+\s*%", html))


def check_html_structure(html: str) -> dict:
    """Structural-quality checks on the HTML."""
    return {
        "has_wrapper":      "font-family" in html,
        "has_insight_box":  "💡" in html or "insight" in html.lower(),
        "has_warning_box":  "⚠" in html or "warning" in html.lower() or "risk" in html.lower(),
        "has_badges":       "border-radius:12px" in html or "border-radius: 12px" in html,
        "has_price_format": check_price_format(html),
        "has_percentage":   check_has_percentage(html),
        "html_length":      len(html),
    }


def analyze_interpretation(html: str, expected_fields: list[str]) -> dict:
    """Check whether the LLM actually interpreted the expected fields."""
    plain = re.sub(r"<[^>]+>", "", html).lower()
    found = {f: f.lower() in plain for f in expected_fields}
    coverage = sum(found.values()) / len(found) if found else 0
    return {"field_coverage": coverage, "found_fields": found}


# ─── Test-case scaffolding ───────────────────────────────────────────────────

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
        if not any(t in self.tools_called for t in self.case.expected_tools):
            return False
        return True

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

    # ═══════════════════════════════════════════════════════════════
    # JUP_PRICES — Price API v3
    # Parametreler: mints (string, max 50 comma-separated)
    # Returned fields: usdPrice, priceChange24h, liquidity, decimals, blockId, createdAt
    # ═══════════════════════════════════════════════════════════════

    TestCase(
        id="price_01_single_sol",
        tool="jup_prices",
        query="SOL'un şu anki fiyatı nedir?",
        expected_tools=["jup_prices"],
        check_html=["sol", "solana", "$"],
        check_fields=["usdPrice", "priceChange", "liquidity"],
        description="Single token — the SOL price",
    ),
    TestCase(
        id="price_02_single_bonk",
        tool="jup_prices",
        query=f"Bu mint adresinin fiyatı nedir: {BONK}",
        expected_tools=["jup_prices", "jup_search"],
        check_html=["bonk", "$"],
        check_fields=["usdPrice", "priceChange"],
        description="Mint adresiyle tek token sorgusu",
    ),
    TestCase(
        id="price_03_batch_5_tokens",
        tool="jup_prices",
        query="SOL, BONK, JUP, WIF ve RAY fiyatlarını aynı anda göster",
        expected_tools=["jup_prices"],
        check_html=["sol", "bonk", "jup", "wif"],
        check_fields=["usdPrice", "priceChange"],
        description="Batch — 5 tokens at once",
    ),
    TestCase(
        id="price_04_stablecoins",
        tool="jup_prices",
        query="USDC ve USDT şu an ne kadar? Peg'den saptılar mı?",
        expected_tools=["jup_prices"],
        check_html=["usdc", "usdt", "$1"],
        check_fields=["usdPrice", "liquidity"],
        description="Stablecoin — peg check",
    ),
    TestCase(
        id="price_05_lsts",
        tool="jup_prices",
        query=f"JitoSOL ve mSOL fiyatları nedir, SOL'dan ne kadar premium var?",
        expected_tools=["jup_prices"],
        check_html=["jitosol", "msol", "sol", "premium"],
        check_fields=["usdPrice", "liquidity"],
        description="LST prices plus the SOL premium",
    ),
    TestCase(
        id="price_06_meme_coins",
        tool="jup_prices",
        query="Fartcoin, POPCAT ve BOME fiyatlarını karşılaştır",
        expected_tools=["jup_prices"],
        check_html=["fartcoin", "popcat", "bome"],
        check_fields=["usdPrice", "priceChange"],
        description="Meme coins compared in one batch",
    ),
    TestCase(
        id="price_07_missing_token",
        tool="jup_prices",
        query=f"Bu token adresi için fiyat al: {FAKE}",
        expected_tools=["jup_prices", "jup_search"],
        check_html=[],
        check_fields=[],
        description="Invalid mint — how the response is handled",
    ),
    TestCase(
        id="price_08_liquidity_focus",
        tool="jup_prices",
        query="JUP ve RAY'ın likidite durumu nedir? Yüksek slippage riski var mı?",
        expected_tools=["jup_prices"],
        check_html=["jup", "ray", "likidite", "slippage"],
        check_fields=["liquidity", "usdPrice"],
        description="Liquidity-focused analysis",
    ),
    TestCase(
        id="price_09_24h_change_focus",
        tool="jup_prices",
        query="SOL, JUP ve BONK son 24 saatte ne kadar değişti?",
        expected_tools=["jup_prices"],
        check_html=["24", "%", "sol", "jup", "bonk"],
        check_fields=["priceChange24h"],
        description="Focused on the 24h change",
    ),

    # ═══════════════════════════════════════════════════════════════
    # JUP_SEARCH — Token arama v2
    # Parameters: query (string — name, symbol, or up to 100 comma-separated mints)
    # Returned fields: id, name, symbol, usdPrice, mcap, fdv, holderCount, liquidity,
    #   circSupply, totalSupply, stats5m/1h/6h/24h (SwapStats), audit (isSus,
    #   mintAuthorityDisabled, freezeAuthorityDisabled, topHoldersPercentage,
    #   devBalancePercentage, devMints), organicScore, organicScoreLabel,
    #   isVerified, tags, launchpad, graduatedPool, firstPool, dev, twitter, website
    # ═══════════════════════════════════════════════════════════════

    TestCase(
        id="search_01_symbol_bonk",
        tool="jup_search",
        query="BONK token hakkında her şeyi anlat",
        expected_tools=["jup_search"],
        check_html=["bonk", "holder", "organik", "audit", "verified"],
        check_fields=["organicScore", "holderCount", "audit", "mintAuthorityDisabled", "isVerified"],
        description="Sembolle arama — tam analiz",
    ),
    TestCase(
        id="search_02_by_mint_address",
        tool="jup_search",
        query=f"Mint adresi {JUP} olan tokenı ara ve analiz et",
        expected_tools=["jup_search"],
        check_html=["jupiter", "jup"],
        check_fields=["organicScore", "isVerified"],
        description="Searching directly by mint address",
    ),
    TestCase(
        id="search_03_audit_focus",
        tool="jup_search",
        query="WIF tokeninin mint ve freeze authority durumu nedir? Güvenli mi?",
        expected_tools=["jup_search"],
        check_html=["mint", "freeze", "authority", "wif"],
        check_fields=["mintAuthorityDisabled", "freezeAuthorityDisabled", "topHoldersPercentage", "isSus"],
        must_warn=False,
        description="Security question focused on the audit fields",
    ),
    TestCase(
        id="search_04_holder_distribution",
        tool="jup_search",
        query="BONK'un holder dağılımı nasıl? En büyük cüzdanlar ne kadar tutuyor?",
        expected_tools=["jup_search"],
        check_html=["holder", "bonk", "%"],
        check_fields=["holderCount", "topHoldersPercentage", "devBalancePercentage"],
        description="Holder distribution and concentration analysis",
    ),
    TestCase(
        id="search_05_organic_score",
        tool="jup_search",
        query="JUP tokeninin organik ticaret skoru nedir? Gerçek mi bot mu?",
        expected_tools=["jup_search"],
        check_html=["organik", "jup", "bot"],
        check_fields=["organicScore", "organicScoreLabel", "numOrganicBuyers"],
        description="Question focused on the organic score",
    ),
    TestCase(
        id="search_06_supply_analysis",
        tool="jup_search",
        query="JUP tokeninin dolaşımdaki arzı nedir? FDV vs market cap farkı ne?",
        expected_tools=["jup_search"],
        check_html=["jup", "fdv", "mcap", "arz"],
        check_fields=["circSupply", "totalSupply", "fdv", "mcap"],
        description="Supply analysis — FDV vs mcap dilution risk",
    ),
    TestCase(
        id="search_07_social_links",
        tool="jup_search",
        query="BONK tokeninin resmi Twitter ve website adresi nedir?",
        expected_tools=["jup_search"],
        check_html=["twitter", "website", "bonk"],
        check_fields=["twitter", "website", "telegram"],
        description="Sosyal medya ve web linkleri",
    ),
    TestCase(
        id="search_08_launchpad",
        tool="jup_search",
        query="Fartcoin pump.fun'dan mı çıktı? Graduated pool var mı?",
        expected_tools=["jup_search"],
        check_html=["fartcoin", "pump"],
        check_fields=["launchpad", "graduatedPool", "firstPool"],
        description="Launchpad ve graduation durumu",
    ),
    TestCase(
        id="search_09_multi_timeframe_stats",
        tool="jup_search",
        query="BONK'un 5 dakika, 1 saat, 6 saat ve 24 saat işlem istatistiklerini karşılaştır",
        expected_tools=["jup_search"],
        check_html=["5", "1", "6", "24", "bonk", "alış", "satış"],
        check_fields=["stats5m", "stats1h", "stats6h", "stats24h", "buyVolume", "sellVolume"],
        description="SwapStats compared across several intervals",
    ),
    TestCase(
        id="search_10_buy_sell_pressure",
        tool="jup_search",
        query="SOL'da şu an alış mı satış mı baskısı var? Organik alıcı sayısı ne?",
        expected_tools=["jup_search"],
        check_html=["alış", "satış", "sol"],
        check_fields=["buyVolume", "sellVolume", "numOrganicBuyers", "numNetBuyers", "numBuys", "numSells"],
        description="Buy/sell pressure and organic-flow analysis",
    ),
    TestCase(
        id="search_11_batch_mints",
        tool="jup_search",
        query=f"Bu üç token hakkında bilgi ver: {SOL}, {BONK}, {JUP}",
        expected_tools=["jup_search"],
        check_html=["sol", "bonk", "jup"],
        check_fields=["usdPrice", "organicScore"],
        description="Multi-mint batch query",
    ),
    TestCase(
        id="search_12_dev_wallet",
        tool="jup_search",
        query="BONK'un geliştirici cüzdanı ne kadar token tutuyor? Dev dump riski var mı?",
        expected_tools=["jup_search"],
        check_html=["dev", "geliştirici", "bonk"],
        check_fields=["devBalancePercentage", "devMints"],
        description="Developer wallet analizi",
    ),
    TestCase(
        id="search_13_token_age",
        tool="jup_search",
        query="BONK ne zaman ilk havuzunu açtı? Token ne kadar eski?",
        expected_tools=["jup_search"],
        check_html=["bonk", "pool", "havuz"],
        check_fields=["firstPool", "createdAt"],
        description="Token age and first-pool date",
    ),
    TestCase(
        id="search_14_risky_new_token",
        tool="jup_search",
        query="TRUMP tokeni güvenli mi? Mint authority kapalı mı?",
        expected_tools=["jup_search"],
        check_html=["trump", "mint", "freeze"],
        check_fields=["mintAuthorityDisabled", "freezeAuthorityDisabled", "topHoldersPercentage", "isSus"],
        must_warn=True,
        description="Risk analizi — TRUMP token",
    ),
    TestCase(
        id="search_15_unknown_symbol",
        tool="jup_search",
        query="XRANDOMZTOKEN123 diye bir token var mı Jupiter'de?",
        expected_tools=["jup_search"],
        check_html=[],
        check_fields=[],
        description="Searching for a non-existent token — edge case",
    ),

    # ═══════════════════════════════════════════════════════════════
    # JUP_RECENT — recently launched tokens
    # Parameters: none (the API always returns 30 tokens)
    # Returned fields: id, name, symbol, decimals, usdPrice, liquidity, holderCount,
    #   organicScore, organicScoreLabel, isVerified, firstPool.id, firstPool.createdAt
    # ═══════════════════════════════════════════════════════════════

    TestCase(
        id="recent_01_show_new",
        tool="jup_recent",
        query="En son çıkan yeni tokenları listele",
        expected_tools=["jup_recent"],
        check_html=["yeni", "token"],
        check_fields=["firstPool", "organicScore", "isVerified"],
        must_warn=True,
        description="New-token listing — a risk warning is expected",
    ),
    TestCase(
        id="recent_02_launch_date",
        tool="jup_recent",
        query="Bu hafta ilk likidite havuzunu açan tokenları göster ve kaç saat önce çıktıklarını söyle",
        expected_tools=["jup_recent"],
        check_html=["saat", "dakika", "pool", "havuz"],
        check_fields=["firstPool", "createdAt"],
        description="Reading firstPool.createdAt — when did it launch",
    ),
    TestCase(
        id="recent_03_organic_filter",
        tool="jup_recent",
        query="Son çıkan tokenlar arasında organik skoru yüksek olanlar var mı?",
        expected_tools=["jup_recent"],
        check_html=["organik", "skor"],
        check_fields=["organicScore", "organicScoreLabel"],
        description="Organic filter across new tokens",
    ),
    TestCase(
        id="recent_04_liquidity_safety",
        tool="jup_recent",
        query="Yeni çıkan tokenlardan hangilerinde en az $10.000 likidite var?",
        expected_tools=["jup_recent"],
        check_html=["likidite", "10", "$"],
        check_fields=["liquidity"],
        must_warn=True,
        description="Liquidity-threshold filter plus a rug-risk warning",
    ),
    TestCase(
        id="recent_05_pump_fun_detection",
        tool="jup_recent",
        query="Son çıkan tokenlar arasında pump.fun'dan gelenler hangileri?",
        expected_tools=["jup_recent"],
        check_html=["pump"],
        check_fields=["firstPool", "organicScore"],
        must_warn=True,
        description="Pump.fun token tespiti yeni listede",
    ),
    TestCase(
        id="recent_06_holder_check",
        tool="jup_recent",
        query="Yeni çıkan tokenlardan hangilerinin holder sayısı 100'den az?",
        expected_tools=["jup_recent"],
        check_html=["holder", "100"],
        check_fields=["holderCount"],
        must_warn=True,
        description="Holder-count filter — few holders means high risk",
    ),

    # ═══════════════════════════════════════════════════════════════
    # JUP_TRENDING — trending by category and interval
    # Parametreler:
    #   category: toptrending | toptraded | toporganicscore (zorunlu)
    #   interval: 5m | 1h | 6h | 24h (zorunlu)
    #   limit: 1-100 (opsiyonel, default 50)
    # Returned fields: the full MintInformation — usdPrice, stats{interval}, organicScore,
    #   audit, isVerified, launchpad, graduatedPool, mcap, holderCount
    # ═══════════════════════════════════════════════════════════════

    # toptrending × 4 interval
    TestCase(
        id="trend_01_trending_5m",
        tool="jup_trending",
        query="Son 5 dakikada Jupiter'de en hızlı yükselen tokenlar hangileri?",
        expected_tools=["jup_trending"],
        check_html=["5", "dakika", "token"],
        check_fields=["organicScore", "usdPrice", "stats5m"],
        description="toptrending — 5 dakika",
    ),
    TestCase(
        id="trend_02_trending_1h",
        tool="jup_trending",
        query="Son 1 saatin Jupiter trend tokenlarını göster",
        expected_tools=["jup_trending"],
        check_html=["1", "saat", "trend"],
        check_fields=["organicScore", "usdPrice"],
        description="toptrending — 1 saat",
    ),
    TestCase(
        id="trend_03_trending_6h",
        tool="jup_trending",
        query="Son 6 saatin trend tokenları neler?",
        expected_tools=["jup_trending"],
        check_html=["6", "saat", "trend"],
        check_fields=["organicScore", "usdPrice"],
        description="toptrending — 6 saat",
    ),
    TestCase(
        id="trend_04_trending_24h",
        tool="jup_trending",
        query="Bu gün Jupiter'de en çok trend olan tokenlar hangileri?",
        expected_tools=["jup_trending"],
        check_html=["24", "trend"],
        check_fields=["organicScore", "usdPrice", "stats24h"],
        description="toptrending — 24 saat",
    ),

    # toptraded × 4 interval
    TestCase(
        id="trend_05_traded_5m",
        tool="jup_trending",
        query="Son 5 dakikada Jupiter'de hacim bazında en çok işlem gören tokenlar",
        expected_tools=["jup_trending"],
        check_html=["hacim", "işlem", "token"],
        check_fields=["buyVolume", "sellVolume", "numBuys", "numSells"],
        description="toptraded — 5 minutes, volume-focused",
    ),
    TestCase(
        id="trend_06_traded_1h",
        tool="jup_trending",
        query="Son 1 saatte en yüksek swap hacmine sahip tokenlar",
        expected_tools=["jup_trending"],
        check_html=["hacim", "swap", "1"],
        check_fields=["buyVolume", "sellVolume"],
        description="toptraded — 1 saat",
    ),
    TestCase(
        id="trend_07_traded_6h",
        tool="jup_trending",
        query="Son 6 saatte Jupiter'de en çok alım satım yapılan tokenlar",
        expected_tools=["jup_trending"],
        check_html=["alım", "satım", "6"],
        check_fields=["buyVolume", "sellVolume", "numTraders"],
        description="toptraded — 6 saat",
    ),
    TestCase(
        id="trend_08_traded_24h",
        tool="jup_trending",
        query="Son 24 saatin en yüksek hacimli tokenlarını listele",
        expected_tools=["jup_trending"],
        check_html=["hacim", "24", "$"],
        check_fields=["buyVolume", "sellVolume"],
        description="toptraded — 24 saat",
    ),

    # toporganicscore × 4 interval
    TestCase(
        id="trend_09_organic_5m",
        tool="jup_trending",
        query="Son 5 dakikada bot olmayan gerçek kullanıcıların ilgi gösterdiği tokenlar",
        expected_tools=["jup_trending"],
        check_html=["organik", "gerçek", "bot"],
        check_fields=["organicScore", "organicScoreLabel", "numOrganicBuyers"],
        description="toporganicscore — 5 dakika",
    ),
    TestCase(
        id="trend_10_organic_1h",
        tool="jup_trending",
        query="Son 1 saatte wash trading olmayan, organik talep gören tokenlar",
        expected_tools=["jup_trending"],
        check_html=["organik", "wash", "talep"],
        check_fields=["organicScore", "organicScoreLabel"],
        description="toporganicscore — 1 saat",
    ),
    TestCase(
        id="trend_11_organic_6h",
        tool="jup_trending",
        query="6 saatlik pencerede organik skoru en yüksek tokenlar hangileri?",
        expected_tools=["jup_trending"],
        check_html=["organik", "skor", "6"],
        check_fields=["organicScore", "numOrganicBuyers"],
        description="toporganicscore — 6 saat",
    ),
    TestCase(
        id="trend_12_organic_24h",
        tool="jup_trending",
        query="Bugün gerçek kullanıcı talebini en çok çeken tokenlar",
        expected_tools=["jup_trending"],
        check_html=["gerçek", "kullanıcı", "talep"],
        check_fields=["organicScore", "organicScoreLabel"],
        description="toporganicscore — 24 saat",
    ),

    # Limit parametresi
    TestCase(
        id="trend_13_limit_3",
        tool="jup_trending",
        query="Jupiter'in şu anki top 3 trend tokenı nedir?",
        expected_tools=["jup_trending"],
        check_html=["trend"],
        check_fields=["organicScore", "usdPrice"],
        description="limit=3 — few results",
    ),
    TestCase(
        id="trend_14_limit_50",
        tool="jup_trending",
        query="24 saatlik top traded listesinden ilk 50 tokeni göster",
        expected_tools=["jup_trending"],
        check_html=["token", "hacim"],
        check_fields=["buyVolume", "sellVolume"],
        description="limit=50 — the default limit",
    ),

    # Harder: how the category is chosen
    TestCase(
        id="trend_15_compare_categories",
        tool="jup_trending",
        query="1 saatlik toptrending ve 1 saatlik toptraded listelerini getir ve ikisini karşılaştır — hangi tokenlar her iki listede de çıkıyor?",
        expected_tools=["jup_trending"],
        check_html=["trend", "hacim"],
        check_fields=["organicScore", "buyVolume"],
        description="Two categories compared — must make a separate API call for each",
    ),
    TestCase(
        id="trend_16_audit_in_trending",
        tool="jup_trending",
        query="Son 1 saatin trend tokenlarından hangilerinin mint authority'si hâlâ açık (risk)?",
        expected_tools=["jup_trending"],
        check_html=["mint", "authority", "risk"],
        check_fields=["mintAuthorityDisabled", "isSus", "audit"],
        must_warn=True,
        description="Trending + audit filtresi",
    ),
    TestCase(
        id="trend_17_pumpfun_in_trending",
        tool="jup_trending",
        query="Son 24 saatte trend olan tokenlar arasında pump.fun'dan gelenler var mı? Graduate olmuş mu?",
        expected_tools=["jup_trending"],
        check_html=["pump", "launchpad"],
        check_fields=["launchpad", "graduatedPool"],
        description="Trending + launchpad filtresi",
    ),
    TestCase(
        id="trend_18_organic_vs_traded",
        tool="jup_trending",
        query="1 saatlik organik token listesindeki tokenları toptraded ile karşılaştır — gerçek talep vs saf hacim farkı",
        expected_tools=["jup_trending"],
        check_html=["organik", "hacim"],
        check_fields=["organicScore", "buyVolume", "numOrganicBuyers"],
        description="Organic versus Traded compared",
    ),

    # ═══════════════════════════════════════════════════════════════
    # JUP_TOKENS_TAG — token listing by tag
    # Parametreler: query ('lst' veya 'verified'), limit (opsiyonel)
    # Returned fields: the full MintInformation plus apy (lst only)
    # ═══════════════════════════════════════════════════════════════

    TestCase(
        id="tag_01_lst_basic",
        tool="jup_tokens_tag",
        query="Jupiter'deki liquid staking tokenlarını listele",
        expected_tools=["jup_tokens_tag"],
        check_html=["liquid staking", "lst", "sol"],
        check_fields=["apy", "usdPrice", "liquidity", "organicScore"],
        description="LST temel sorgu",
    ),
    TestCase(
        id="tag_02_lst_apy",
        tool="jup_tokens_tag",
        query="Hangi liquid staking tokeni en yüksek APY veriyor? JitoSOL, mSOL veya bSOL?",
        expected_tools=["jup_tokens_tag"],
        check_html=["apy", "jitosol", "msol"],
        check_fields=["apy"],
        description="LST APYs compared",
    ),
    TestCase(
        id="tag_03_lst_vs_sol_price",
        tool="jup_tokens_tag",
        query="LST tokenların SOL'a göre değeri ne kadar? Staking premium hesapla",
        expected_tools=["jup_tokens_tag", "jup_prices"],
        check_html=["sol", "lst", "premium", "staking"],
        check_fields=["usdPrice", "apy"],
        description="LST price — computing the SOL premium",
    ),
    TestCase(
        id="tag_04_lst_liquidity",
        tool="jup_tokens_tag",
        query="LST tokenlardan hangisi en derin likiditeye sahip?",
        expected_tools=["jup_tokens_tag"],
        check_html=["likidite", "lst"],
        check_fields=["liquidity", "usdPrice"],
        description="LST liquidity compared",
    ),
    TestCase(
        id="tag_05_lst_organic_score",
        tool="jup_tokens_tag",
        query="LST tokenların organik ticaret skoru nasıl? Wash trading var mı?",
        expected_tools=["jup_tokens_tag"],
        check_html=["organik", "lst"],
        check_fields=["organicScore", "organicScoreLabel"],
        description="LST organik skor analizi",
    ),
    TestCase(
        id="tag_06_verified_basic",
        tool="jup_tokens_tag",
        query="Jupiter'de doğrulanmış token listesini göster",
        expected_tools=["jup_tokens_tag"],
        check_html=["verified", "doğrulanmış"],
        check_fields=["isVerified", "organicScore"],
        description="Verified temel sorgu",
    ),
    TestCase(
        id="tag_07_verified_audit",
        tool="jup_tokens_tag",
        query="Doğrulanmış tokenların audit durumu nedir? Hepsinin mint authority kapalı mı?",
        expected_tools=["jup_tokens_tag"],
        check_html=["mint", "freeze", "audit", "doğrulanmış"],
        check_fields=["mintAuthorityDisabled", "freezeAuthorityDisabled", "topHoldersPercentage"],
        description="Verified + audit analizi",
    ),
    TestCase(
        id="tag_08_verified_mcap",
        tool="jup_tokens_tag",
        query="Doğrulanmış tokenlar arasında market cap'e göre sıralama yap",
        expected_tools=["jup_tokens_tag"],
        check_html=["mcap", "market cap", "doğrulanmış"],
        check_fields=["mcap", "fdv", "usdPrice"],
        description="Verified tokens ranked by market cap",
    ),

    # ═══════════════════════════════════════════════════════════════
    # JUP_VERIFY_ELIGIBILITY — token verification eligibility
    # Parametreler: token_id (mint adresi, zorunlu)
    # Returned fields: tokenExists, isVerified, canVerify, canMetadata,
    #   verificationError, metadataError
    # NOT: JUPITER_API_KEY gerektirir
    # ═══════════════════════════════════════════════════════════════

    TestCase(
        id="verify_01_known_verified",
        tool="jup_verify_eligibility",
        query=f"BONK tokeni ({BONK}) Jupiter'de verify edilmiş mi? Verify eligibility kontrol et",
        expected_tools=["jup_verify_eligibility", "jup_search"],
        check_html=["bonk", "verified", "doğrulama"],
        check_fields=["tokenExists", "isVerified", "canVerify"],
        description="Zaten verified olan token — kontrol",
    ),
    TestCase(
        id="verify_02_wif_check",
        tool="jup_verify_eligibility",
        query=f"WIF tokeni ({WIF}) Jupiter verification'a uygun mu?",
        expected_tools=["jup_verify_eligibility", "jup_search"],
        check_html=["wif", "verify", "doğrulama"],
        check_fields=["tokenExists", "canVerify", "canMetadata"],
        description="WIF verification eligibility check",
    ),
    TestCase(
        id="verify_03_error_message",
        tool="jup_verify_eligibility",
        query=f"Bu mint adresi için verification uygunluğunu kontrol et ve neden verify edilemiyorsa açıkla: {FAKE}",
        expected_tools=["jup_verify_eligibility", "jup_search"],
        check_html=["hata", "bulunamadı", "geçersiz"],
        check_fields=["tokenExists", "verificationError"],
        description="Invalid mint — reading verificationError",
    ),

    # ═══════════════════════════════════════════════════════════════
    # EDGE CASES — exercising tool selection and interpretation
    # ═══════════════════════════════════════════════════════════════

    TestCase(
        id="edge_01_ambiguous_price_or_search",
        tool="jup_prices|jup_search",
        query="BONK ne kadar?",
        expected_tools=["jup_prices", "jup_search"],
        check_html=["bonk", "$"],
        check_fields=["usdPrice"],
        description="Vague price question — which tool gets picked?",
    ),
    TestCase(
        id="edge_02_chain_search_and_price",
        tool="jup_search+jup_prices",
        query="SOL ve JUP'u karşılaştır: hangisi daha güvenli, hangisi daha pahalı?",
        expected_tools=["jup_search", "jup_prices"],
        check_html=["sol", "jup", "güvenli"],
        check_fields=["audit", "usdPrice", "organicScore"],
        description="Multi-tool chain — security plus price",
    ),
    TestCase(
        id="edge_03_trending_vs_organic",
        tool="jup_trending",
        query="Şu an gerçekten popüler olan mi yoksa sadece bot aktivitesi olan mı tokenlar trend ediyor?",
        expected_tools=["jup_trending"],
        check_html=["organik", "bot", "trend"],
        check_fields=["organicScore", "organicScoreLabel", "numOrganicBuyers"],
        description="Trending quality — telling bots from organic",
    ),
    TestCase(
        id="edge_04_new_token_full_risk",
        tool="jup_recent",
        query="En son çıkan tokeni bul ve tüm risk faktörlerini değerlendir",
        expected_tools=["jup_recent"],
        check_html=["risk", "rug", "güvenli"],
        check_fields=["liquidity", "holderCount", "organicScore", "isVerified"],
        must_warn=True,
        description="Newest token — full risk assessment",
    ),
    TestCase(
        id="edge_05_lst_vs_direct_staking",
        tool="jup_tokens_tag",
        query="Liquid staking mi yoksa doğrudan SOL stake mi daha mantıklı? LST APY'leri göster",
        expected_tools=["jup_tokens_tag"],
        check_html=["apy", "staking", "sol", "lst"],
        check_fields=["apy", "usdPrice"],
        description="LST versus staking directly",
    ),
    TestCase(
        id="edge_06_search_nonexistent",
        tool="jup_search",
        query="'NONEXISTENTCOIN999' diye bir token Jupiter'de var mı?",
        expected_tools=["jup_search"],
        check_html=[],
        check_fields=[],
        description="Non-existent token — how does the LLM answer?",
    ),
    TestCase(
        id="edge_07_many_tokens_price",
        tool="jup_prices",
        query=f"Şu tokenların hepsinin fiyatını tek seferde al: SOL, BONK, JUP, WIF, RAY, USDC, USDT, JitoSOL, mSOL",
        expected_tools=["jup_prices"],
        check_html=["sol", "bonk", "jup", "wif", "ray"],
        check_fields=["usdPrice", "liquidity"],
        description="9 token batch — max limit testi",
    ),
    TestCase(
        id="edge_08_trending_audit_chain",
        tool="jup_trending",
        query="Son 1 saatin trend tokenlarından en güvenli olanı hangisi? Audit ve holder konsantrasyonuna bak",
        expected_tools=["jup_trending"],
        check_html=["audit", "holder", "güvenli"],
        check_fields=["mintAuthorityDisabled", "topHoldersPercentage", "organicScore"],
        description="Trending chained with a security filter",
    ),
]


# ─── Test runner ─────────────────────────────────────────────────────────────

async def run_single(case: TestCase, query_fn, fast: bool = False) -> TestResult:
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
                wait = 20 + attempt * 10
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
    print(f"   Sorgu    : {r.case.query[:80]}{'...' if len(r.case.query)>80 else ''}")
    print(f"   Tools    : {r.tools_called}  {tool_ok}")
    print(f"   Time     : {r.duration:.1f}s  |  HTML: {len(r.html)} chars")

    if r.error:
        print(f"   HATA     : {r.error}")
        return

    if not fast:
        # HTML kalite kontrolleri
        struct = r.structure()
        checks = []
        if struct["has_wrapper"]:         checks.append("🏗wrapper")
        if struct["has_insight_box"]:     checks.append("💡insight")
        if struct["has_warning_box"]:     checks.append("⚠warning")
        if struct["has_badges"]:          checks.append("🏷badges")
        if struct["has_price_format"]:    checks.append("💲fiyat")
        if struct["has_percentage"]:      checks.append("📊%")
        print(f"   HTML     : {' | '.join(checks) or 'ZAYIF'}")

        # Anahtar kelime kontrolleri
        if r.case.check_html:
            kw_results = r.html_checks()
            found = [k for k, v in kw_results.items() if v]
            missing = [k for k, v in kw_results.items() if not v]
            if found:    print(f"   ✓ Kelime : {found}")
            if missing:  print(f"   ✗ Eksik  : {missing}")

        # Field coverage
        if r.case.check_fields:
            fa = r.field_analysis()
            cov = fa["field_coverage"]
            bar = "█" * int(cov * 10) + "░" * (10 - int(cov * 10))
            missing_fields = [f for f, ok in fa["found_fields"].items() if not ok]
            print(f"   Yorum    : [{bar}] {cov*100:.0f}%", end="")
            if missing_fields: print(f"  ✗ {missing_fields}")
            else: print()

        # Warning check
        if r.case.must_warn:
            has_warn = struct["has_warning_box"] or "risk" in r.plain.lower() or "uyarı" in r.plain.lower() or "rug" in r.plain.lower()
            print(f"   Risk uyar: {'✅ VAR' if has_warn else '❌ YOK (OLMALI)'}")

        # Response excerpt (first 250 chars)
        excerpt = r.plain[:250].strip().replace("\n", " ")
        print(f"   Response : {excerpt}...")


async def run_all(tool_filter: str | None, fast: bool):
    from orchestrator import query

    cases = ALL_CASES
    if tool_filter:
        cases = [c for c in cases if tool_filter in c.tool or tool_filter == c.id]

    print(f"\n{'═'*70}")
    print(f"OPRAI Jupiter LLM End-to-End Test Suite")
    print(f"{'═'*70}")
    print(f"Toplam test: {len(cases)} | Mod: {'FAST' if fast else 'DETAYLI'}")
    if tool_filter: print(f"Filtre: {tool_filter}")

    results: list[TestResult] = []
    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] running {case.id}...", end="", flush=True)
        r = await run_single(case, query, fast)
        results.append(r)
        print_result(r, fast)
        # Rate limit: wait 1.5s between API calls
        if i < len(cases):
            await asyncio.sleep(1.5)

    # ── Summary report ──────────────────────────────────────────────────────
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    tool_ok = sum(1 for r in results if r.tool_ok)
    errors = sum(1 for r in results if r.error)
    avg_duration = sum(r.duration for r in results) / total if total else 0
    avg_html = sum(len(r.html) for r in results if r.html) / max(1, total - errors)

    # Mean field coverage
    coverage_vals = []
    for r in results:
        if r.case.check_fields and not r.error:
            fa = r.field_analysis()
            coverage_vals.append(fa["field_coverage"])
    avg_coverage = sum(coverage_vals) / len(coverage_vals) if coverage_vals else 0

    print(f"\n{'═'*70}")
    print(f"OVERALL RESULT")
    print(f"{'═'*70}")
    print(f"  Toplam test   : {total}")
    print(f"  ✅ Passed     : {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"  🎯 Right tool : {tool_ok}/{total} ({tool_ok/total*100:.0f}%)")
    print(f"  ❌ Hata       : {errors}")
    print(f"  ⏱ Avg time  : {avg_duration:.1f}s/test")
    print(f"  📄 Ort HTML   : {avg_html:.0f} karakter")
    print(f"  📊 Yorum kap. : {avg_coverage*100:.0f}%")

    print(f"\n{'─'*70}")
    print("FAILED TESTS:")
    failed = [r for r in results if not r.passed]
    if not failed:
        print("  All passed! 🎉")
    else:
        for r in failed:
            reason = r.error or f"Beklenen: {r.case.expected_tools}, Gelen: {r.tools_called}"
            print(f"  ✗ {r.case.id}: {reason[:100]}")

    print(f"\n{'─'*70}")
    print("PER-TOOL SUMMARY:")
    from collections import defaultdict
    by_tool: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_tool[r.case.tool].append(r)
    for tool, tool_results in sorted(by_tool.items()):
        ok = sum(1 for r in tool_results if r.passed)
        print(f"  {tool:35s}: {ok}/{len(tool_results)} ✅")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", help="Test only this tool (e.g. jup_trending)")
    parser.add_argument("--fast", action="store_true", help="Short output")
    parser.add_argument("--id", help="Belirli bir test ID'si")
    args = parser.parse_args()

    filter_val = args.id or args.tool
    asyncio.run(run_all(filter_val, args.fast))

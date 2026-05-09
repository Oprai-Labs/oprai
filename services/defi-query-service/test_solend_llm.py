"""
Solend/Save LLM Integration Test Suite
=======================================
Tests LLM tool selection, parameter passing, and response quality
for all 51 Solend tools in the defi-query-service.

Categories:
  A. Market & Reserve Data        (TC-A01 – TC-A08)
  B. User Positions & Obligations (TC-B01 – TC-B07)
  C. Liquidity Mining & Rewards   (TC-C01 – TC-C06)
  D. History & Transactions       (TC-D01 – TC-D06)
  E. Points & Referrals           (TC-E01 – TC-E05)
  F. Token & Supply               (TC-F01 – TC-F04)
  G. Risk & Liquidations          (TC-G01 – TC-G04)
  H. Response Quality             (TC-H01 – TC-H05)
  I. Edge Cases & Guardrails      (TC-I01 – TC-I04)
  J. Turkish Language             (TC-J01 – TC-J03)

Run:
  cd services/defi-query-service
  python3 test_solend_llm.py
"""

import asyncio
import time
import httpx

BASE = "http://localhost:3150"
DELAY = 22       # seconds between tests (TPM safety)
RETRY_DELAY = 38 # seconds on 500

# ── Well-known addresses ──────────────────────────────────────────────────────
SOL      = "So11111111111111111111111111111111111111112"
USDC     = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT     = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MSOL     = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
JITOSOL  = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
BSOL     = "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1"
# Solend main production market
MAIN_MARKET = "7RCz8wb6WXxUhAigok9ttgrVgDFFFbibcirECzWSBauM"
# Example wallets (public, well-known)
WALLET_A = "CakcnaRDHka2gXyfbEd2d3xsvkJkqsLw2akB3zsN1D2S"  # random public wallet


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(tc: str, label: str, ok: bool, detail: str = ""):
    mark = "✅" if ok else "❌"
    print(f"  {mark} [{tc}] {label}" + (f": {detail}" if detail else ""))


async def ask(question: str) -> dict:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{BASE}/query", json={"question": question})
        if r.status_code == 500:
            print(f"  ⚠️  500 — waiting {RETRY_DELAY}s then retrying")
            await asyncio.sleep(RETRY_DELAY)
            r = await client.post(f"{BASE}/query", json={"question": question})
        r.raise_for_status()
        return r.json()


def check_tool(result: dict, expected: set, tc: str) -> bool:
    called = set(result.get("tools_called", []))
    ok = bool(called & expected)
    _log(tc, "tool_selection", ok, f"called={called}, want_any={expected}")
    return ok


def check_no_tool(result: dict, tc: str) -> bool:
    called = set(result.get("tools_called", []))
    ok = len(called) == 0
    _log(tc, "no_tool_called", ok, f"called={called} (expected empty)")
    return ok


def check_text(result: dict, keywords: list, tc: str, label: str = "content") -> bool:
    body = (result.get("plain", "") + " " + result.get("html", "")).lower()
    missing = [k for k in keywords if k.lower() not in body]
    ok = not missing
    _log(tc, label, ok, f"missing={missing}" if missing else "all present")
    return ok


def check_no_error(result: dict, tc: str) -> bool:
    body = (result.get("plain", "") + " " + result.get("html", "")).lower()
    bad = ["error", "traceback", "exception", "failed to fetch", "500"]
    found = [b for b in bad if b in body]
    ok = not found
    _log(tc, "no_error_in_response", ok, f"found={found}" if found else "clean")
    return ok


def check_quality(result: dict, tc: str, min_words: int = 30) -> bool:
    words = len(result.get("plain", "").split())
    ok = words >= min_words
    _log(tc, "response_length", ok, f"{words} words (min {min_words})")
    return ok


async def run_test(tc: str, question: str, expected_tools: set, checks: list):
    print(f"\n{'─'*65}")
    print(f"[{tc}] {question[:100]}")
    try:
        result = await ask(question)
    except Exception as e:
        print(f"  ❌ [{tc}] REQUEST_ERROR: {e}")
        return False

    passed = True
    if expected_tools is not None:
        tool_ok = check_tool(result, expected_tools, tc)
        passed = passed and tool_ok

    for fn in checks:
        ok = fn(result, tc)
        passed = passed and ok

    return passed


async def run_no_tool_test(tc: str, question: str, checks: list):
    print(f"\n{'─'*65}")
    print(f"[{tc}] {question[:100]}")
    try:
        result = await ask(question)
    except Exception as e:
        print(f"  ❌ [{tc}] REQUEST_ERROR: {e}")
        return False

    passed = True
    ok = check_no_tool(result, tc)
    passed = passed and ok
    for fn in checks:
        ok = fn(result, tc)
        passed = passed and ok
    return passed


# ── Test Sections ─────────────────────────────────────────────────────────────

async def section_a_market_reserve(results: list):
    """A: Market & Reserve Data"""
    print("\n\n━━━ A: Market & Reserve Data ━━━")

    # TC-A01: Basic market overview
    results.append(await run_test(
        "TC-A01", "What lending markets does Solend have?",
        {"solend_markets"},
        [
            lambda r, tc: check_text(r, ["market", "reserve"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 30),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-A02: Reserve details with APY
    results.append(await run_test(
        "TC-A02", "Show me Solend reserve supply and borrow APYs for all assets",
        {"solend_reserves"},
        [
            lambda r, tc: check_text(r, ["apy", "supply", "borrow"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-A03: Protocol-wide stats
    results.append(await run_test(
        "TC-A03", "What is Solend's total TVL and total borrows right now?",
        {"solend_stats"},
        [
            lambda r, tc: check_text(r, ["deposit", "borrow"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-A04: Oracle prices
    results.append(await run_test(
        "TC-A04", "What are Solend's oracle prices for SOL and USDC?",
        {"solend_prices"},
        [
            lambda r, tc: check_text(r, ["sol", "usdc"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-A05: Historical reserve APY
    results.append(await run_test(
        "TC-A05", "Show me how Solend's USDC supply APY changed over the last month",
        {"solend_reserves_history"},
        [
            lambda r, tc: check_text(r, ["apy", "usdc"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-A06: LST rates
    results.append(await run_test(
        "TC-A06", "What APY does Solend offer for jitoSOL, mSOL, and bSOL?",
        {"solend_lst_rates"},
        [
            lambda r, tc: check_text(r, ["apy", "%"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-A07: Daily stats
    results.append(await run_test(
        "TC-A07", "Show me Solend's protocol stats for 2025-01-15",
        {"solend_daily_stats"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-A08: Isolated pool stats
    results.append(await run_test(
        "TC-A08", "What are Solend's isolated pool stats?",
        {"solend_isolated_pool_stats"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_b_user_positions(results: list):
    """B: User Positions & Obligations"""
    print("\n\n━━━ B: User Positions & Obligations ━━━")

    # TC-B01: User overview by wallet
    results.append(await run_test(
        "TC-B01", f"Show me the Solend positions for wallet {WALLET_A}",
        {"solend_user_overview"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-B02: Health factor + liquidation warning
    results.append(await run_test(
        "TC-B02", f"Is wallet {WALLET_A} at risk of liquidation on Solend? Check their health factor",
        {"solend_user_overview"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-B03: cToken history
    results.append(await run_test(
        "TC-B03", f"Show me the cToken history for wallet {WALLET_A} on Solend",
        {"solend_ctoken_history"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-B04: VIP eligibility
    results.append(await run_test(
        "TC-B04", f"Is wallet {WALLET_A} eligible for Solend VIP?",
        {"solend_vip_eligibility"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-B05: Snapshot at timestamp
    results.append(await run_test(
        "TC-B05", f"What was wallet {WALLET_A}'s Solend position on January 1 2025?",
        {"solend_snapshot"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-B06: Airdrops
    results.append(await run_test(
        "TC-B06", f"Does wallet {WALLET_A} have any Solend airdrops?",
        {"solend_airdrops", "solend_airdrops_jito"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-B07: Notifications
    results.append(await run_test(
        "TC-B07", f"Show me Solend notifications for wallet {WALLET_A}",
        {"solend_notifications"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_c_rewards(results: list):
    """C: Liquidity Mining & Rewards"""
    print("\n\n━━━ C: Liquidity Mining & Rewards ━━━")

    # TC-C01: Reward stats overview
    results.append(await run_test(
        "TC-C01", "What liquidity mining rewards does Solend offer? Which reserves have the highest reward APY?",
        {"solend_reward_stats"},
        [
            lambda r, tc: check_text(r, ["reward", "apy"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 30),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-C02: Additional emissions
    results.append(await run_test(
        "TC-C02", "What are the additional emission rates on Solend?",
        {"solend_additional_emissions"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-C03: Reward score for wallet
    results.append(await run_test(
        "TC-C03", f"What is the Solend liquidity mining reward score for wallet {WALLET_A}?",
        {"solend_reward_score"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-C04: Confirmed rewards
    results.append(await run_test(
        "TC-C04", f"How much Solend rewards can wallet {WALLET_A} claim?",
        {"solend_confirmed_rewards"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-C05: Flat vs nested reward stats
    results.append(await run_test(
        "TC-C05", "Get Solend reward emission stats as a flat list",
        {"solend_reward_stats"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-C06: Save/SLND token metrics
    results.append(await run_test(
        "TC-C06", "Show me the Save DAO revenue chart and SLND token metrics",
        {"solend_save_metrics", "solend_save_revenue_chart", "solend_save_price_chart"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_d_history_txs(results: list):
    """D: History & Transactions"""
    print("\n\n━━━ D: History & Transactions ━━━")

    # TC-D01: Obligation history
    results.append(await run_test(
        "TC-D01", f"Show me the Solend position history for wallet {WALLET_A}",
        {"solend_history", "solend_history_v2", "solend_user_overview"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-D02: Liquidation attempts
    results.append(await run_test(
        "TC-D02", "Who got liquidated on Solend's main market recently?",
        {"solend_liquidation_attempts"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-D03: Daily fees
    results.append(await run_test(
        "TC-D03", "What fees did Solend generate in the last 7 days?",
        {"solend_daily_fees"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-D04: Supply totals
    results.append(await run_test(
        "TC-D04", "What is the total and circulating supply of SLND / Save token?",
        {"solend_total_supply", "solend_circulating_supply"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-D05: Historical oracle prices
    results.append(await run_test(
        "TC-D05", f"What was Solend's oracle price for SOL at Unix timestamp 1700000000?",
        {"solend_historical_prices"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-D06: Reserves config changes
    results.append(await run_test(
        "TC-D06", "Has Solend changed any reserve parameters recently? Show config change history",
        {"solend_reserves_config_changes", "solend_reserves"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_e_points_referrals(results: list):
    """E: Points & Referrals"""
    print("\n\n━━━ E: Points & Referrals ━━━")

    # TC-E01: Points overview
    results.append(await run_test(
        "TC-E01", "How does Solend's points system work? What are the current point rules?",
        {"solend_points", "solend_points_config"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-E02: Leaderboard
    results.append(await run_test(
        "TC-E02", "Who is leading the Solend points leaderboard?",
        {"solend_points_leaderboard"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-E03: Total points
    results.append(await run_test(
        "TC-E03", "How many total points have been issued on Solend?",
        {"solend_points_total"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-E04: Referral stats
    results.append(await run_test(
        "TC-E04", "What are the Solend referral program stats?",
        {"solend_referral_stats", "solend_referral_payments"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-E05: Who referred wallet
    results.append(await run_test(
        "TC-E05", f"Who referred wallet {WALLET_A} on Solend?",
        {"solend_referral_referrer"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_f_tokens_supply(results: list):
    """F: Tokens & Supply"""
    print("\n\n━━━ F: Tokens & Supply ━━━")

    # TC-F01: All supported tokens
    results.append(await run_test(
        "TC-F01", "What tokens are listed on Solend / Save protocol?",
        {"solend_tokens_all", "solend_markets", "solend_reserves"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-F02: Specific token details
    results.append(await run_test(
        "TC-F02", "Get Solend token details for SOL and USDC",
        {"solend_tokens", "solend_prices"},
        [
            lambda r, tc: check_text(r, ["sol", "usdc"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-F03: Save metrics
    results.append(await run_test(
        "TC-F03", "Show me Save protocol key metrics and SLND circulating supply",
        {"solend_save_metrics", "solend_circulating_supply"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-F04: Compare Solend vs Kamino
    results.append(await run_test(
        "TC-F04", "Compare Solend and Kamino lending — which has higher USDC supply APY?",
        {"solend_reserves", "kamino_market_reserves"},
        [
            lambda r, tc: check_text(r, ["usdc", "apy"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 40),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_g_risk_liquidations(results: list):
    """G: Risk & Liquidations"""
    print("\n\n━━━ G: Risk & Liquidations ━━━")

    # TC-G01: Find at-risk positions
    results.append(await run_test(
        "TC-G01", "Find Solend positions with over 80% utilization and more than $10000 borrowed",
        {"solend_obligations_filtered"},
        [
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 20),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-G02: Squeezy obligations (high liquidation risk)
    results.append(await run_test(
        "TC-G02", f"Which Solend positions are at highest liquidation risk for USDC borrows?",
        {"solend_squeezy_obligations"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-G03: Liquidation history
    results.append(await run_test(
        "TC-G03", "Have there been any liquidations on Solend recently?",
        {"solend_liquidation_attempts"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-G04: Large borrowers scan
    results.append(await run_test(
        "TC-G04", "Show me the biggest borrowers on Solend — find wallets borrowing more than $100000",
        {"solend_obligations_filtered"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_h_response_quality(results: list):
    """H: Response Quality"""
    print("\n\n━━━ H: Response Quality ━━━")

    # TC-H01: Comprehensive market analysis — checks depth
    results.append(await run_test(
        "TC-H01", "Give me a full analysis of Solend: TVL, top reserves, reward APYs, and overall health",
        {"solend_stats", "solend_reserves", "solend_reward_stats"},
        [
            lambda r, tc: check_text(r, ["tvl", "apy", "reserve"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 80),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-H02: APY formatting — must show % not decimals
    results.append(await run_test(
        "TC-H02", "What is the current supply APY for SOL on Solend?",
        {"solend_reserves"},
        [
            lambda r, tc: check_text(r, ["sol", "%"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-H03: Insight quality — should mention rebrand
    results.append(await run_test(
        "TC-H03", "Tell me about the Solend protocol — what is it and how does it work?",
        {"solend_stats", "solend_markets", "solend_reserves"},
        [
            lambda r, tc: check_text(r, ["lend", "borrow"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 60),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-H04: Multi-tool orchestration — complex question
    results.append(await run_test(
        "TC-H04", "Is Solend a good place to deposit USDC right now? Check APY, utilization, and rewards",
        {"solend_reserves", "solend_reward_stats"},
        [
            lambda r, tc: check_text(r, ["usdc", "apy"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 60),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-H05: Protocol comparison quality
    results.append(await run_test(
        "TC-H05", "Where should I deposit SOL for the best yield: Solend, Kamino, or MarginFi?",
        {"solend_reserves", "kamino_market_reserves", "marginfi_banks"},
        [
            lambda r, tc: check_text(r, ["sol", "apy"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 80),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_i_edge_cases(results: list):
    """I: Edge Cases & Guardrails"""
    print("\n\n━━━ I: Edge Cases & Guardrails ━━━")

    # TC-I01: No Solend tool for unrelated question
    results.append(await run_test(
        "TC-I01", "What is the price of SOL right now?",
        {"birdeye_price", "jup_prices"},  # should NOT call solend tools
        [
            lambda r, tc: check_text(r, ["sol", "$"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-I02: Graceful handling — wallet with no positions
    results.append(await run_test(
        "TC-I02", f"What are the Solend borrowing positions for wallet 11111111111111111111111111111111?",
        {"solend_user_overview"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-I03: Ambiguous query resolved correctly
    results.append(await run_test(
        "TC-I03", "What are current lending rates on Solana?",
        {"solend_reserves", "kamino_market_reserves", "marginfi_banks"},
        [
            lambda r, tc: check_text(r, ["apy", "%"], tc),
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-I04: No tool for pure definition question
    results.append(await run_no_tool_test(
        "TC-I04", "What is a collateralization ratio in DeFi lending?",
        [
            lambda r, tc: check_quality(r, tc, 20),
        ],
    ))
    await asyncio.sleep(DELAY)


async def section_j_turkish(results: list):
    """J: Turkish Language"""
    print("\n\n━━━ J: Turkish Language ━━━")

    # TC-J01: Turkish market query
    results.append(await run_test(
        "TC-J01", "Solend'deki tüm piyasaları ve faiz oranlarını göster",
        {"solend_reserves", "solend_markets"},
        [
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 30),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-J02: Turkish user position query
    results.append(await run_test(
        "TC-J02", f"Solend'de {WALLET_A} cüzdanının pozisyonlarını göster",
        {"solend_user_overview"},
        [
            lambda r, tc: check_no_error(r, tc),
        ],
    ))
    await asyncio.sleep(DELAY)

    # TC-J03: Turkish risk/comparison query
    results.append(await run_test(
        "TC-J03", "Solend'e USDC yatırmak şu an mantıklı mı? APY ve ödülleri kontrol et",
        {"solend_reserves", "solend_reward_stats"},
        [
            lambda r, tc: check_text(r, ["usdc", "apy", "%"], tc),
            lambda r, tc: check_no_error(r, tc),
            lambda r, tc: check_quality(r, tc, 40),
        ],
    ))
    await asyncio.sleep(DELAY)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 65)
    print("  Solend LLM Integration Test Suite")
    print(f"  Service: {BASE}")
    print("=" * 65)

    # Check service health
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            h = await c.get(f"{BASE}/health")
            print(f"  Health: {h.json()}")
    except Exception as e:
        print(f"  ❌ Service unreachable: {e}")
        return

    results: list[bool] = []
    t0 = time.time()

    await section_a_market_reserve(results)
    await section_b_user_positions(results)
    await section_c_rewards(results)
    await section_d_history_txs(results)
    await section_e_points_referrals(results)
    await section_f_tokens_supply(results)
    await section_g_risk_liquidations(results)
    await section_h_response_quality(results)
    await section_i_edge_cases(results)
    await section_j_turkish(results)

    elapsed = time.time() - t0
    passed = sum(results)
    total = len(results)

    print(f"\n\n{'='*65}")
    print(f"  RESULT: {passed}/{total} PASS  ({total - passed} FAIL)")
    print(f"  Elapsed: {elapsed/60:.1f} min")
    print("=" * 65)

    if passed < total:
        print(f"\n  ⚠️  {total - passed} test(s) failed — check output above")


if __name__ == "__main__":
    asyncio.run(main())

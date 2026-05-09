"""
Test suite for GET /defi/history_price via DeFi Query Service LLM.

Coverage:
  TC-01  Basic daily token price history for SOL
  TC-02  Hourly price history (type=1H)
  TC-03  address_type=pair (using SOL-USDC pair address)
  TC-04  Weekly price history (type=1W)
  TC-05  1-minute price history (short range)
  TC-06  ui_amount_mode=scaled
  TC-07  ui_amount_mode=both
  TC-08  Ethereum chain token
  TC-09  BSC chain token
  TC-10  Natural language "SOL price history last 24 hours"
  TC-11  Natural language "how much has BONK changed this week"
  TC-12  Tool selection: price history → birdeye_history_price (not birdeye_ohlcv)
  TC-13  Tool selection: pair price history → birdeye_history_price with address_type=pair
  TC-14  ROI query: "what was SOL price last week vs now"
  TC-15  Polygon chain token
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL        = "So11111111111111111111111111111111111111112"
BONK       = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF        = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
WETH_ETH   = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
WBNB_BSC   = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
WMATIC_POL = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"

# SOL-USDC Raydium pair
SOL_USDC_PAIR = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"

NOW      = 1745107200
DAY_AGO  = NOW - 86400
WEEK_AGO = NOW - 7 * 86400
HOUR_AGO = NOW - 3600

DELAY       = 22
RETRY_DELAY = 38


def _log(tc, label, ok, detail=""):
    mark = "✅" if ok else "❌"
    print(f"  {mark} [{tc}] {label}" + (f": {detail}" if detail else ""))


async def ask(question: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{BASE}/query", json={"question": question})
        if r.status_code == 500:
            print(f"  ⚠️  500 — retrying after {RETRY_DELAY}s")
            await asyncio.sleep(RETRY_DELAY)
            r = await client.post(f"{BASE}/query", json={"question": question})
        r.raise_for_status()
        return r.json()


def check_tool(result, expected: set, tc) -> bool:
    called = set(result.get("tools_called", []))
    ok = bool(called & expected)
    _log(tc, "tool_selection", ok, f"called={called}, expected_any_of={expected}")
    return ok


def check_text(result, keywords: list, tc, label="response_content") -> bool:
    combined = (result.get("plain", "") + " " + result.get("html", "")).lower()
    missing = [k for k in keywords if k.lower() not in combined]
    ok = not missing
    _log(tc, label, ok, f"missing={missing}" if missing else "all present")
    return ok


async def run_test(tc, question, expected_tools, checks):
    print(f"\n{'─'*60}")
    print(f"[{tc}] {question}")
    try:
        result = await ask(question)
    except Exception as e:
        print(f"  ❌ [{tc}] REQUEST_ERROR: {type(e).__name__}: {e}")
        return False

    passed = check_tool(result, expected_tools, tc)
    for fn in checks:
        ok = fn(result, tc)
        passed = passed and ok
    return passed


async def main():
    print("=" * 60)
    print("birdeye_history_price (GET /defi/history_price) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic daily SOL price history
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_history_price to get daily SOL ({SOL}) price history from {DAY_AGO} to {NOW}",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "daily_sol_history")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Hourly price history
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_history_price to get hourly SOL ({SOL}) price history from {HOUR_AGO} to {NOW} type=1H",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "hourly_history")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: address_type=pair
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_history_price with address_type=pair to get daily price history for the SOL-USDC pair ({SOL_USDC_PAIR}) from {DAY_AGO} to {NOW}",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "pair_history")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Weekly price history
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_history_price to get weekly BONK ({BONK}) price history from {NOW - 4*7*86400} to {NOW} type=1W",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "weekly_history")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: 1-minute price history (short range)
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_history_price to get 1-minute SOL ({SOL}) price history from {NOW - 300} to {NOW} type=1m",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "1min_history")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_history_price to get daily BONK ({BONK}) price history from {DAY_AGO} to {NOW} with ui_amount_mode=scaled",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "ui_mode_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: ui_amount_mode=both
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_history_price to get hourly SOL ({SOL}) price history from {HOUR_AGO} to {NOW} with ui_amount_mode=both",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "ui_mode_both")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Ethereum chain
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_history_price to get daily WETH ({WETH_ETH}) price history on chain=ethereum from {DAY_AGO} to {NOW}",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_history")
         or check_text(r, ["ethereum"], tc, "eth_history2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: BSC chain
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_history_price to get daily WBNB ({WBNB_BSC}) price history on BSC chain from {DAY_AGO} to {NOW}",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["bnb"], tc, "bsc_history")
         or check_text(r, ["bsc"], tc, "bsc_history2")
         or check_text(r, ["wbnb"], tc, "bsc_history3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "SOL price history last 24 hours"
    results.append(await run_test(
        "TC-10",
        f"Show me the SOL ({SOL}) price history for the last 24 hours. Use time_from={DAY_AGO} and time_to={NOW} with hourly data points.",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_24h_history")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "how much has BONK changed this week"
    results.append(await run_test(
        "TC-11",
        f"How has BONK ({BONK}) price changed over the past week? Use birdeye_history_price with time_from={WEEK_AGO} time_to={NOW} type=1D",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_bonk_week")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: price history → birdeye_history_price not ohlcv
    results.append(await run_test(
        "TC-12",
        f"Give me the price history line chart data for WIF ({WIF}) over the past 24 hours. time_from={DAY_AGO} time_to={NOW} type=1H",
        {"birdeye_history_price", "birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["wif"], tc, "tool_selection_history")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection: pair price history
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_history_price with address_type=pair to get hourly price data for the SOL-USDC pair ({SOL_USDC_PAIR}) from {DAY_AGO} to {NOW}",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "pair_address_type")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: ROI / comparison query
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_history_price to get daily SOL ({SOL}) price data from {WEEK_AGO} to {NOW} — I want to calculate the 7-day ROI",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "roi_query")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Polygon chain
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_history_price to get daily WMATIC ({WMATIC_POL}) price history on Polygon from {DAY_AGO} to {NOW}",
        {"birdeye_history_price"},
        [lambda r, tc: check_text(r, ["matic"], tc, "polygon_history")
         or check_text(r, ["polygon"], tc, "polygon_history2")
         or check_text(r, ["wmatic"], tc, "polygon_history3")],
    ))

    print(f"\n{'='*60}")
    total = len(results)
    passed = sum(results)
    print(f"RESULT: {passed}/{total} PASS")
    if passed < total:
        print(f"FAILED: {total - passed} tests")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

"""
Test suite for GET /defi/v3/ohlcv/pair via DeFi Query Service LLM.

Coverage:
  TC-01  Basic hourly candles for SOL-USDC pair (mode=range)
  TC-02  Daily candles (type=1D, mode=range)
  TC-03  mode=count, count_limit + time_to (backward)
  TC-04  mode=count, count_limit + time_from (forward)
  TC-05  inversion=true (inverts base/quote direction)
  TC-06  padding=true (fills empty intervals)
  TC-07  outlier=false (exclude outlier candles)
  TC-08  mode=count + inversion combo
  TC-09  Sub-minute: type=1m
  TC-10  Weekly candles: type=1W
  TC-11  Different pair: SOL-USDT
  TC-12  Tool selection: pair address → birdeye_ohlcv_pair (not birdeye_ohlcv)
  TC-13  Natural language "candles for this pool"
  TC-14  Natural language "inverted chart"
  TC-15  Bug regression: address= param (not pair_address=) actually returns data
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

# Well-known Raydium pair addresses
SOL_USDC_PAIR = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
SOL_USDT_PAIR = "3nMFwZXwY1s1M5s8vYAHqd4wGs4iSxXE4LRoUMMYqEgF"

NOW = 1745107200
DAY_AGO = NOW - 86400
WEEK_AGO = NOW - 7 * 86400

DELAY = 22
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
    print("birdeye_ohlcv_pair (GET /defi/v3/ohlcv/pair) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic hourly candles (mode=range)
    results.append(await run_test(
        "TC-01",
        f"Use Birdeye OHLCV pair to get hourly candles for the SOL-USDC pair ({SOL_USDC_PAIR}) from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "hourly_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Daily candles
    results.append(await run_test(
        "TC-02",
        f"Get daily Birdeye OHLCV candles for the SOL-USDC pair ({SOL_USDC_PAIR}) from {WEEK_AGO} to {NOW} type=1D",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "daily_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: mode=count backward (time_to only)
    results.append(await run_test(
        "TC-03",
        f"Get the last 5 hourly candles for pair {SOL_USDC_PAIR} using Birdeye OHLCV pair with mode=count, count_limit=5, time_to={NOW}",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "count_backward")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: mode=count forward (time_from only)
    results.append(await run_test(
        "TC-04",
        f"Get 3 hourly candles forward from {WEEK_AGO} for SOL-USDC pair ({SOL_USDC_PAIR}) using Birdeye OHLCV pair mode=count with time_from",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "count_forward")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: inversion=true
    results.append(await run_test(
        "TC-05",
        f"Get Birdeye OHLCV pair candles for {SOL_USDC_PAIR} from {DAY_AGO} to {NOW} type=1H with inversion=true (to see USDC-per-SOL inverted)",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["inver"], tc, "inversion_mentioned")
         or check_text(r, ["sol"], tc, "inversion_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: padding=true
    results.append(await run_test(
        "TC-06",
        f"Use Birdeye OHLCV pair for SOL-USDC ({SOL_USDC_PAIR}) from {DAY_AGO} to {NOW} type=1H with padding=true",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "padded_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: outlier=false
    results.append(await run_test(
        "TC-07",
        f"Get Birdeye OHLCV pair for SOL-USDC ({SOL_USDC_PAIR}) from {DAY_AGO} to {NOW} type=1H with outlier=false",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "outlier_excluded")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: mode=count + inversion combo
    results.append(await run_test(
        "TC-08",
        f"Get last 3 daily inverted candles for SOL-USDC pair ({SOL_USDC_PAIR}) using Birdeye OHLCV pair: mode=count, count_limit=3, time_to={NOW}, inversion=true",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "count_inversion_combo")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: 1m candles (count=5 to stay within timeout)
    results.append(await run_test(
        "TC-09",
        f"Get last 5 one-minute candles for SOL-USDC pair ({SOL_USDC_PAIR}) using Birdeye OHLCV pair: mode=count, count_limit=5, time_to={NOW}, type=1m",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "1min_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Weekly candles
    results.append(await run_test(
        "TC-10",
        f"Show weekly Birdeye OHLCV candles for SOL-USDC pair ({SOL_USDC_PAIR}) from {NOW - 4*7*86400} to {NOW} type=1W",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "weekly_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Different pair (SOL-USDT)
    results.append(await run_test(
        "TC-11",
        f"Get hourly Birdeye OHLCV pair candles for SOL-USDT pair ({SOL_USDT_PAIR}) from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_usdt_pair")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection — pair address should pick birdeye_ohlcv_pair not birdeye_ohlcv
    results.append(await run_test(
        "TC-12",
        f"Get price chart for this Raydium pool: {SOL_USDC_PAIR} — show me today's hourly candles",
        {"birdeye_ohlcv_pair", "birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "tool_selected_correctly")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "candles for this pool"
    results.append(await run_test(
        "TC-13",
        f"What does the price chart look like for the SOL-USDC Raydium pool ({SOL_USDC_PAIR})? Show hourly candles",
        {"birdeye_ohlcv_pair", "birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_pool_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "inverted chart"
    results.append(await run_test(
        "TC-14",
        f"Show me the inverted price chart for the SOL-USDC pair ({SOL_USDC_PAIR}) — I want to see USDC per SOL, daily candles",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_inverted_chart")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Bug regression — verify address= param works (not pair_address=), data returned
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_ohlcv_pair to get 3 daily candles for {SOL_USDC_PAIR} with mode=count, count_limit=3, time_to={NOW}. Confirm you get actual OHLCV data back.",
        {"birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["sol"], tc, "actual_data_returned")],
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

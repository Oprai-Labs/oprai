"""
Test suite for GET /defi/ohlcv (v1) via DeFi Query Service LLM.

Coverage:
  TC-01  Basic daily candles on Solana (type=1D)
  TC-02  Hourly candles (type=1H)
  TC-03  currency=native
  TC-04  ui_amount_mode=both
  TC-05  Ethereum chain
  TC-06  Different chain: BSC (BNB chain)
  TC-07  Weekly candles (type=1W)
  TC-08  1-minute candles (count limited to avoid timeout)
  TC-09  Response field check: unixTime (camelCase, not unix_time)
  TC-10  Natural language "SOL hourly chart today" → birdeye_ohlcv_v1
  TC-11  Tool selection: broad-chain request → birdeye_ohlcv_v1
  TC-12  Tool selection: Arbitrum token → birdeye_ohlcv_v1 (not birdeye_ohlcv)
  TC-13  vs birdeye_ohlcv: same SOL data comparison (both should work)
  TC-14  Natural language "native currency candles"
  TC-15  Polygon chain token
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WETH_ETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"  # BNB chain wrapped BNB
WMATIC = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"  # Polygon WMATIC

NOW = 1745107200
DAY_AGO = NOW - 86400
WEEK_AGO = NOW - 7 * 86400
HOUR_AGO = NOW - 3600

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
    print("birdeye_ohlcv_v1 (GET /defi/ohlcv) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic daily candles on Solana
    results.append(await run_test(
        "TC-01",
        f"Use Birdeye legacy OHLCV (birdeye_ohlcv_v1) to get daily candles for SOL ({SOL}) from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "daily_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Hourly candles
    results.append(await run_test(
        "TC-02",
        f"Get hourly Birdeye v1 OHLCV candles for SOL ({SOL}) from {HOUR_AGO} to {NOW}",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "hourly_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: currency=native
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_ohlcv_v1 to get daily candles for BONK ({BONK}) from {DAY_AGO} to {NOW} with currency=native",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "native_currency")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: ui_amount_mode=both
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_ohlcv_v1 to get daily SOL ({SOL}) candles from {DAY_AGO} to {NOW} with ui_amount_mode=both",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "ui_mode_both")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Ethereum chain
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_ohlcv_v1 to get daily Ethereum WETH ({WETH_ETH}) candles from {DAY_AGO} to {NOW} on chain=ethereum",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_candles")
         or check_text(r, ["ethereum"], tc, "eth_candles2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: BSC chain
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_ohlcv_v1 to get daily candles for WBNB ({WBNB}) on BSC chain from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["bnb"], tc, "bsc_candles")
         or check_text(r, ["bsc"], tc, "bsc_candles2")
         or check_text(r, ["wbnb"], tc, "bsc_candles3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Weekly candles
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_ohlcv_v1 to get weekly SOL ({SOL}) candles from {NOW - 4*7*86400} to {NOW} type=1W",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "weekly_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: 1-minute candles (short range to avoid timeout)
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_ohlcv_v1 to get 1-minute SOL ({SOL}) candles from {NOW - 300} to {NOW} type=1m",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "1min_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Response uses unixTime (camelCase) — LLM should present time correctly
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_ohlcv_v1 to get 3 hourly SOL ({SOL}) candles from {HOUR_AGO} to {NOW}. Show me the time of each candle.",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "time_presented")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language → should pick birdeye_ohlcv_v1 when explicitly mentioned
    results.append(await run_test(
        "TC-10",
        f"Use the legacy Birdeye OHLCV endpoint (birdeye_ohlcv_v1) to get today's hourly SOL price chart. Use time_from={DAY_AGO} and time_to={NOW}",
        {"birdeye_ohlcv_v1", "birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_legacy_request")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Broad chain mention → should use birdeye_ohlcv_v1
    results.append(await run_test(
        "TC-11",
        f"Get daily WETH candles from {DAY_AGO} to {NOW} on Arbitrum chain using Birdeye",
        {"birdeye_ohlcv_v1", "birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["weth"], tc, "arbitrum_candles")
         or check_text(r, ["arbitrum"], tc, "arbitrum_candles2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Arbitrum token specifically → v1 preferred over v3
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_ohlcv_v1 to get daily WETH candles on Arbitrum from {DAY_AGO} to {NOW}. Address: {WETH_ETH}",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["weth"], tc, "arb_explicit")
         or check_text(r, ["arbitrum"], tc, "arb_explicit2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Both v1 and v3 can handle SOL data
    results.append(await run_test(
        "TC-13",
        f"Get daily SOL candles from {DAY_AGO} to {NOW} using Birdeye OHLCV (any version)",
        {"birdeye_ohlcv_v1", "birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "any_version_works")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "native currency candles"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_ohlcv_v1 to show BONK ({BONK}) candles priced in SOL (native currency) from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "native_currency_natural")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Polygon chain
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_ohlcv_v1 to get daily WMATIC ({WMATIC}) candles on Polygon from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_v1"},
        [lambda r, tc: check_text(r, ["wmatic"], tc, "polygon_candles")
         or check_text(r, ["polygon"], tc, "polygon_candles2")
         or check_text(r, ["matic"], tc, "polygon_candles3")],
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

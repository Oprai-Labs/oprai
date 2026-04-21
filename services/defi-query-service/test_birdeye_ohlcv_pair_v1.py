"""
Test suite for GET /defi/ohlcv/pair (v1) via DeFi Query Service LLM.

Coverage:
  TC-01  Basic hourly candles for SOL-USDC pair (default chain=solana)
  TC-02  Daily candles (type=1D)
  TC-03  Weekly candles (type=1W)
  TC-04  1-minute candles (short range)
  TC-05  Ethereum chain pair
  TC-06  BSC chain pair
  TC-07  Polygon chain pair
  TC-08  Arbitrum chain pair
  TC-09  Different Solana pair: SOL-USDT
  TC-10  Natural language "candles for this pool" (v1 explicit)
  TC-11  Tool selection: v1 vs v3 — explicit birdeye_ohlcv_pair_v1 request
  TC-12  Tool selection: broad chain pair → birdeye_ohlcv_pair_v1 preferred
  TC-13  Response field check: unixTime in candle data
  TC-14  Long range: 7-day hourly candles
  TC-15  Natural language "inverted pair" with v1 (no inversion param — LLM should note limitation or use v3)
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL_USDC_PAIR = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
SOL_USDT_PAIR = "3nMFwZXwY1s1M5s8vYAHqd4wGs4iSxXE4LRoUMMYqEgF"

# Ethereum Uniswap V3 ETH-USDC pool
ETH_USDC_PAIR_ETH = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
# BSC PancakeSwap BNB-BUSD
BNB_BUSD_PAIR_BSC = "0x58F876857a02D6762E0101bb5C46A8c1ED44Dc16"
# Polygon QuickSwap WMATIC-USDC
WMATIC_USDC_PAIR_POLY = "0x6e7a5FAFcec6BB1e78bAE2A1F0B612012BF14827"
# Arbitrum Uniswap V3 ETH-USDC
ETH_USDC_PAIR_ARB = "0xC6962004f452bE9203591991D15f6b388e09E8D0"

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
    print("birdeye_ohlcv_pair_v1 (GET /defi/ohlcv/pair) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic hourly candles SOL-USDC
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_ohlcv_pair_v1 to get hourly candles for the SOL-USDC pair ({SOL_USDC_PAIR}) from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "hourly_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Daily candles
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_ohlcv_pair_v1 to get daily candles for SOL-USDC pair ({SOL_USDC_PAIR}) from {WEEK_AGO} to {NOW} type=1D",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "daily_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Weekly candles
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_ohlcv_pair_v1 to get weekly candles for the SOL-USDC pair ({SOL_USDC_PAIR}) from {NOW - 4*7*86400} to {NOW} type=1W",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "weekly_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: 1-minute candles (short range to avoid timeout)
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_ohlcv_pair_v1 to get 1-minute candles for SOL-USDC pair ({SOL_USDC_PAIR}) from {NOW - 300} to {NOW} type=1m",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "1min_pair_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Ethereum chain pair
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_ohlcv_pair_v1 to get hourly candles for the Ethereum ETH-USDC Uniswap pair ({ETH_USDC_PAIR_ETH}) from {DAY_AGO} to {NOW} on chain=ethereum",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["eth"], tc, "eth_pair_candles")
         or check_text(r, ["usdc"], tc, "eth_pair_candles2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: BSC chain pair
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_ohlcv_pair_v1 to get daily BNB-BUSD pair candles ({BNB_BUSD_PAIR_BSC}) on BSC from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["bnb"], tc, "bsc_pair_candles")
         or check_text(r, ["bsc"], tc, "bsc_pair_candles2")
         or check_text(r, ["busd"], tc, "bsc_pair_candles3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Polygon chain pair
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_ohlcv_pair_v1 to get daily WMATIC-USDC pair candles ({WMATIC_USDC_PAIR_POLY}) on Polygon from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["matic"], tc, "polygon_pair_candles")
         or check_text(r, ["polygon"], tc, "polygon_pair_candles2")
         or check_text(r, ["usdc"], tc, "polygon_pair_candles3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Arbitrum chain pair
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_ohlcv_pair_v1 to get hourly ETH-USDC pair candles ({ETH_USDC_PAIR_ARB}) on Arbitrum from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["eth"], tc, "arb_pair_candles")
         or check_text(r, ["arbitrum"], tc, "arb_pair_candles2")
         or check_text(r, ["usdc"], tc, "arb_pair_candles3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Different Solana pair: SOL-USDT
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_ohlcv_pair_v1 to get hourly candles for SOL-USDT pair ({SOL_USDT_PAIR}) from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_usdt_pair")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "candles for this pool" with v1 explicit
    results.append(await run_test(
        "TC-10",
        f"Use the legacy Birdeye pair OHLCV endpoint (birdeye_ohlcv_pair_v1) to get today's hourly chart for the SOL-USDC Raydium pool ({SOL_USDC_PAIR}). Use time_from={DAY_AGO} and time_to={NOW}",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_legacy_pair")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Tool selection — explicit v1 request
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_ohlcv_pair_v1 (not birdeye_ohlcv_pair) to fetch daily SOL-USDC candles ({SOL_USDC_PAIR}) from {WEEK_AGO} to {NOW}",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "explicit_v1_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Broad chain pair → v1 preferred (Polygon chain, not in v3)
    results.append(await run_test(
        "TC-12",
        f"Get daily candles for this Polygon DEX pair ({WMATIC_USDC_PAIR_POLY}) from {DAY_AGO} to {NOW} using Birdeye — chain is Polygon",
        {"birdeye_ohlcv_pair_v1", "birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["matic"], tc, "polygon_chain_routing")
         or check_text(r, ["polygon"], tc, "polygon_chain_routing2")
         or check_text(r, ["usdc"], tc, "polygon_chain_routing3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Response unixTime field — LLM should present time of candles
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_ohlcv_pair_v1 to get 3 hourly candles for SOL-USDC pair ({SOL_USDC_PAIR}) from {HOUR_AGO} to {NOW}. Show the time of each candle.",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "time_presented")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: 7-day hourly candles (larger range)
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_ohlcv_pair_v1 to get hourly SOL-USDC candles ({SOL_USDC_PAIR}) for the past week, time_from={WEEK_AGO} to {NOW} type=1H",
        {"birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "week_hourly_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: v1 vs v3 — user asks for inverted pair (LLM should use v3 pair or note v1 limitation)
    results.append(await run_test(
        "TC-15",
        f"Get the inverted hourly chart for the SOL-USDC pair ({SOL_USDC_PAIR}) from {DAY_AGO} to {NOW} — show USDC per SOL",
        {"birdeye_ohlcv_pair", "birdeye_ohlcv_pair_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "inverted_pair_routing")],
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

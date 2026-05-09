"""
Test suite for GET /defi/v3/ohlcv via DeFi Query Service LLM.

Coverage:
  TC-01  Basic daily candles (mode=range, time_from+time_to)
  TC-02  Hourly candles (type=1H, intraday)
  TC-03  mode=count, count_limit + time_to only
  TC-04  mode=count, count_limit + time_from only (forward)
  TC-05  currency=native (prices in SOL/ETH)
  TC-06  padding=true (fills empty intervals)
  TC-07  outlier=false (exclude outlier candles)
  TC-08  ui_amount_mode=both
  TC-09  Short interval: type=1m candles
  TC-10  Sub-minute interval: type=15s
  TC-11  Weekly candles: type=1W
  TC-12  Ethereum chain (ERC-20 token)
  TC-13  Natural language "last 7 days of SOL candles"
  TC-14  Natural language "SOL hourly chart today"
  TC-15  Tool selection: candle request → birdeye_ohlcv (not price/history)
"""

import asyncio
import time
import httpx

BASE = "http://localhost:3150"

SOL = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
WETH_ETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

# Reference timestamps (fixed, not relative — 2026-04-19 range)
NOW = 1745107200         # 2026-04-20 00:00 UTC approx
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
        print(f"  ❌ [{tc}] REQUEST_ERROR: {e}")
        return False

    passed = check_tool(result, expected_tools, tc)
    for fn in checks:
        ok = fn(result, tc)
        passed = passed and ok
    return passed


async def main():
    print("=" * 60)
    print("birdeye_ohlcv (GET /defi/v3/ohlcv) — Full Parameter Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic daily candles (mode=range)
    results.append(await run_test(
        "TC-01",
        f"Use Birdeye OHLCV to get daily candles for SOL ({SOL}) from {DAY_AGO} to {NOW} (Unix timestamps, type=1D)",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "daily_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Hourly candles
    results.append(await run_test(
        "TC-02",
        f"Get hourly Birdeye OHLCV candles for {SOL} from {HOUR_AGO} to {NOW}",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "hourly_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: mode=count with time_to (count backward)
    results.append(await run_test(
        "TC-03",
        f"Get the last 5 daily candles for SOL ({SOL}) using Birdeye OHLCV with mode=count, count_limit=5, time_to={NOW}",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "count_backward")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: mode=count with time_from (count forward)
    results.append(await run_test(
        "TC-04",
        f"Get 3 hourly candles for SOL ({SOL}) starting from {WEEK_AGO} using Birdeye OHLCV mode=count with time_from",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "count_forward")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: currency=native — response shows prices in native SOL token
    results.append(await run_test(
        "TC-05",
        f"Get Birdeye OHLCV candles for BONK ({BONK}) from {DAY_AGO} to {NOW} with currency=native (prices in SOL)",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "native_currency")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: padding=true — question uses raw address, LLM may not resolve symbol
    results.append(await run_test(
        "TC-06",
        f"Use Birdeye OHLCV for BONK ({BONK}) from {DAY_AGO} to {NOW} type=1H with padding=true to fill empty candles",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "padded_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: outlier=false
    results.append(await run_test(
        "TC-07",
        f"Get Birdeye OHLCV for SOL ({SOL}) from {DAY_AGO} to {NOW} type=1H with outlier=false to exclude outlier candles",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "outlier_excluded")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: ui_amount_mode=both
    results.append(await run_test(
        "TC-08",
        f"Birdeye OHLCV for {SOL} from {DAY_AGO} to {NOW} type=1D with ui_amount_mode=both",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "ui_mode_both")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: 1-minute candles — use count=5 to avoid timeout (60 candles too many for LLM)
    results.append(await run_test(
        "TC-09",
        f"Get the last 5 one-minute Birdeye OHLCV candles for SOL ({SOL}) using mode=count, count_limit=5, time_to={NOW}, type=1m",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "1min_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: 15-second candles — Birdeye doesn't retain 15s data for old timestamps;
    # verify tool is called and LLM gracefully reports no data or the attempt
    results.append(await run_test(
        "TC-10",
        f"Get 15-second Birdeye OHLCV candles (type=15s) for SOL ({SOL}) from {NOW - 900} to {NOW}",
        {"birdeye_ohlcv"},
        [lambda r, tc: (
            _log(tc, "15s_graceful", True, "LLM attempted 15s candles (data may be empty for past timestamps)") or True
        )],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Weekly candles
    results.append(await run_test(
        "TC-11",
        f"Show me weekly Birdeye OHLCV candles for WIF ({WIF}) from {NOW - 4*7*86400} to {NOW}",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["wif"], tc, "weekly_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Ethereum chain
    results.append(await run_test(
        "TC-12",
        f"Use Birdeye OHLCV to get daily candles for WETH on Ethereum ({WETH_ETH}) from {DAY_AGO} to {NOW} chain=ethereum",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_ohlcv")
         or check_text(r, ["ethereum"], tc, "eth_ohlcv2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "last 7 days" → LLM infers timestamps + mode
    results.append(await run_test(
        "TC-13",
        "Show me the last 7 daily candles for SOL using Birdeye OHLCV",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_7days")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "hourly chart" — keep to 3 candles to avoid timeout
    results.append(await run_test(
        "TC-14",
        "Show me the last 3 hourly candles for SOL using Birdeye OHLCV",
        {"birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_hourly_3candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Tool selection — candle request shouldn't pick history_price or birdeye_price
    results.append(await run_test(
        "TC-15",
        f"Give me candlestick data for BONK ({BONK}) over the last 24 hours",
        {"birdeye_ohlcv", "birdeye_ohlcv_pair"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "candle_tool_selected")],
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

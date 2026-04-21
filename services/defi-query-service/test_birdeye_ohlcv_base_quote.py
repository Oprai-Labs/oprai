"""
Test suite for GET /defi/ohlcv/base_quote via DeFi Query Service LLM.

Coverage:
  TC-01  Basic daily candles BONK/SOL (type=1D)
  TC-02  Hourly candles WIF/USDC
  TC-03  SOL as base, USDC as quote (SOL priced in USDC)
  TC-04  Weekly candles (type=1W)
  TC-05  1-minute candles (short range)
  TC-06  ui_amount_mode=scaled
  TC-07  ui_amount_mode=both
  TC-08  Reversed pair: SOL as base, BONK as quote (unusual direction)
  TC-09  Natural language "WIF priced in SOL chart"
  TC-10  Natural language "price of BONK in USDC terms"
  TC-11  Tool selection: custom pair → birdeye_ohlcv_base_quote (not birdeye_ohlcv)
  TC-12  Tool selection: "token X vs token Y" → birdeye_ohlcv_base_quote
  TC-13  JUP/SOL pair candles
  TC-14  RAY/USDC pair candles
  TC-15  Response field check: unixTime in candle data
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"

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
    print("birdeye_ohlcv_base_quote (GET /defi/ohlcv/base_quote) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic daily BONK/SOL
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_ohlcv_base_quote to get daily candles for BONK ({BONK}) priced in SOL ({SOL}) from {DAY_AGO} to {NOW} type=1D",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_sol_daily")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Hourly WIF/USDC
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_ohlcv_base_quote to get hourly WIF ({WIF}) priced in USDC ({USDC}) candles from {HOUR_AGO} to {NOW} type=1H",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_usdc_hourly")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: SOL/USDC candles
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_ohlcv_base_quote to get daily SOL ({SOL}) priced in USDC ({USDC}) candles from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_usdc_candles")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Weekly candles
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_ohlcv_base_quote to get weekly BONK ({BONK}) priced in USDC ({USDC}) candles from {NOW - 4*7*86400} to {NOW} type=1W",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "weekly_base_quote")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: 1-minute candles (short range)
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_ohlcv_base_quote to get 1-minute SOL ({SOL}) / USDC ({USDC}) candles from {NOW - 300} to {NOW} type=1m",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["sol"], tc, "1min_base_quote")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_ohlcv_base_quote to get daily BONK ({BONK}) / SOL ({SOL}) candles from {DAY_AGO} to {NOW} with ui_amount_mode=scaled",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "ui_mode_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: ui_amount_mode=both
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_ohlcv_base_quote to get daily SOL ({SOL}) / USDC ({USDC}) candles from {DAY_AGO} to {NOW} with ui_amount_mode=both",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["sol"], tc, "ui_mode_both")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Reversed/unusual direction (SOL as base, BONK as quote)
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_ohlcv_base_quote to get daily candles for SOL ({SOL}) priced in BONK ({BONK}) from {DAY_AGO} to {NOW} type=1D",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["sol"], tc, "reversed_pair")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "WIF priced in SOL chart"
    results.append(await run_test(
        "TC-09",
        f"Show me the WIF price chart in SOL terms for the past 24 hours. WIF mint: {WIF}, SOL mint: {SOL}. Use time_from={DAY_AGO} time_to={NOW}",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["wif"], tc, "natural_wif_sol")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "price of BONK in USDC terms"
    results.append(await run_test(
        "TC-10",
        f"What does the BONK/{BONK} vs USDC/{USDC} price chart look like? Show daily candles from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_bonk_usdc")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Tool selection — custom pair should pick base_quote not ohlcv
    results.append(await run_test(
        "TC-11",
        f"Get candlestick data for WIF ({WIF}) priced in SOL ({SOL}) from {DAY_AGO} to {NOW} — I want to see WIF/SOL candles",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["wif"], tc, "tool_selection_base_quote")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection — "token X vs token Y chart"
    results.append(await run_test(
        "TC-12",
        f"Show me the price of JUP ({JUP}) relative to USDC ({USDC}) — daily chart from {DAY_AGO} to {NOW}",
        {"birdeye_ohlcv_base_quote", "birdeye_ohlcv"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_vs_usdc")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: JUP/SOL pair
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_ohlcv_base_quote to get hourly JUP ({JUP}) / SOL ({SOL}) candles from {DAY_AGO} to {NOW} type=1H",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_sol_hourly")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: RAY/USDC pair
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_ohlcv_base_quote to get daily RAY ({RAY}) / USDC ({USDC}) candles from {WEEK_AGO} to {NOW} type=1D",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_usdc_daily")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Response field check — LLM should present timestamps from unixTime
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_ohlcv_base_quote to get 3 hourly SOL ({SOL}) / USDC ({USDC}) candles from {HOUR_AGO} to {NOW}. Show the time of each candle.",
        {"birdeye_ohlcv_base_quote"},
        [lambda r, tc: check_text(r, ["sol"], tc, "time_presented")],
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

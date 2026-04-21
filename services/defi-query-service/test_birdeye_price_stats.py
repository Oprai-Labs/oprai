"""
Test suite for GET /defi/v3/price/stats/single via DeFi Query Service LLM.

Coverage:
  TC-01  SOL price stats (no timeframe)
  TC-02  list_timeframe=1h for BONK
  TC-03  list_timeframe=1h,4h,24h for WIF
  TC-04  list_timeframe=1h,4h,24h,7d for SOL
  TC-05  ui_amount_mode=raw
  TC-06  ui_amount_mode=scaled
  TC-07  list_timeframe=30m,1h,2h for RAY
  TC-08  list_timeframe=24h,2d,3d,7d (longer timeframes)
  TC-09  Natural language "SOL price performance across timeframes"
  TC-10  Natural language "is BONK up on all timeframes"
  TC-11  Natural language "multi-timeframe analysis for WIF"
  TC-12  Tool selection: price stats → birdeye_price_stats
  TC-13  JUP price change % across timeframes
  TC-14  list_timeframe=5m,30m,1h (short intervals)
  TC-15  list_timeframe=1h,24h,7d + ui_amount_mode=raw combo
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"

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
    print("birdeye_price_stats (GET /defi/v3/price/stats/single) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic SOL price stats
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_price_stats to get price statistics for SOL ({SOL})",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_price_stats")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: list_timeframe=1h for BONK
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_price_stats to get BONK ({BONK}) price stats with list_timeframe=1h",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_1h_stats")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: list_timeframe=1h,4h,24h for WIF
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_price_stats to get WIF ({WIF}) price performance with list_timeframe=1h,4h,24h",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_multi_timeframe")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: list_timeframe=1h,4h,24h,7d for SOL
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_price_stats for SOL ({SOL}) with list_timeframe=1h,4h,24h,7d",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_1h_4h_24h_7d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: ui_amount_mode=raw
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_price_stats for SOL ({SOL}) with ui_amount_mode=raw",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_stats_raw")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_price_stats for BONK ({BONK}) with ui_amount_mode=scaled",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_stats_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: list_timeframe=30m,1h,2h for RAY
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_price_stats for RAY ({RAY}) with list_timeframe=30m,1h,2h",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_short_timeframes")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: longer timeframes
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_price_stats for SOL ({SOL}) with list_timeframe=24h,2d,3d,7d",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_long_timeframes")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "price performance across timeframes"
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_price_stats to show SOL ({SOL}) price performance across 1h, 24h, and 7d timeframes",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_price_performance")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "is BONK up on all timeframes"
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_price_stats — is BONK ({BONK}) up or down on 1h, 4h, and 24h timeframes?",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_bonk_up_down")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "multi-timeframe analysis"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_price_stats to do a multi-timeframe price analysis for WIF ({WIF}) with list_timeframe=1h,4h,24h,7d",
        {"birdeye_price_stats", "birdeye_price_stats_multi"},
        [lambda r, tc: check_text(r, ["wif"], tc, "natural_multiframe_wif")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: price stats
    results.append(await run_test(
        "TC-12",
        f"What are the high/low prices and % changes for SOL ({SOL}) over the last 1h, 24h, and 7d?",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["sol"], tc, "price_stats_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: JUP price change across timeframes
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_price_stats to get JUP ({JUP}) price change percentages with list_timeframe=1h,4h,24h",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_price_change")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Short interval timeframes
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_price_stats for BONK ({BONK}) with list_timeframe=5m,30m,1h",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_short_intervals")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: combo timeframe + ui_amount_mode
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_price_stats for SOL ({SOL}) with list_timeframe=1h,24h,7d and ui_amount_mode=raw",
        {"birdeye_price_stats"},
        [lambda r, tc: check_text(r, ["sol"], tc, "combo_timeframe_ui_mode")],
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

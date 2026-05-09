"""
Tests for GET /defi/v3/all-time/trades/single via DeFi Query Service LLM.

TC-01  BONK 24h trade stats
TC-02  SOL 7d trade stats
TC-03  WIF 30d trade stats
TC-04  JUP 1h trade stats
TC-05  time_frame=alltime
TC-06  time_frame=1m
TC-07  time_frame=4h
TC-08  time_frame=1y
TC-09  ui_amount_mode=raw
TC-10  chain=ethereum
TC-11  Natural language "total trades for BONK in 24h"
TC-12  Natural language "all time trading volume for SOL"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
USDC_ETH = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

DELAY       = 22
RETRY_DELAY = 38


def _log(tc, label, ok, detail=""):
    mark = "✅" if ok else "❌"
    print(f"  {mark} [{tc}] {label}" + (f": {detail}" if detail else ""))


async def ask(question: str) -> dict:
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            r = await client.post(f"{BASE}/query", json={"question": question})
        except httpx.ReadTimeout:
            print(f"  ⚠️  ReadTimeout — retrying after {RETRY_DELAY}s")
            await asyncio.sleep(RETRY_DELAY)
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
    print("birdeye_all_time_trades_single (GET /defi/v3/all-time/trades/single) — Tests")
    print("=" * 60)

    results = []

    # TC-01: BONK 24h
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_all_time_trades_single to get BONK ({BONK}) trade stats for 24h (time_frame=24h)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "bonk_24h")
         or check_text(r, ["bonk"], tc, "bonk_24h2")
         or check_text(r, ["volume"], tc, "bonk_24h3")
         or check_text(r, ["count"], tc, "bonk_24h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: SOL 7d
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_all_time_trades_single to get SOL ({SOL}) trade stats for 7 days (time_frame=7d)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "sol_7d")
         or check_text(r, ["sol"], tc, "sol_7d2")
         or check_text(r, ["volume"], tc, "sol_7d3")
         or check_text(r, ["count"], tc, "sol_7d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF 30d
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_all_time_trades_single to get WIF ({WIF}) trade stats over 30 days (time_frame=30d)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "wif_30d")
         or check_text(r, ["wif"], tc, "wif_30d2")
         or check_text(r, ["volume"], tc, "wif_30d3")
         or check_text(r, ["count"], tc, "wif_30d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: JUP 1h
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_all_time_trades_single to get JUP ({JUP}) trade stats for 1 hour (time_frame=1h)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "jup_1h")
         or check_text(r, ["jup"], tc, "jup_1h2")
         or check_text(r, ["volume"], tc, "jup_1h3")
         or check_text(r, ["count"], tc, "jup_1h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: alltime
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_all_time_trades_single to get all-time trade stats for BONK ({BONK}) (time_frame=alltime)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "alltime")
         or check_text(r, ["bonk"], tc, "alltime2")
         or check_text(r, ["volume"], tc, "alltime3")
         or check_text(r, ["all"], tc, "alltime4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: 1m
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_all_time_trades_single to get SOL ({SOL}) trade stats for the last 1 minute (time_frame=1m)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "1m")
         or check_text(r, ["sol"], tc, "1m2")
         or check_text(r, ["volume"], tc, "1m3")
         or check_text(r, ["count"], tc, "1m4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: 4h
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_all_time_trades_single to get BONK ({BONK}) trade stats for 4 hours (time_frame=4h)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "4h")
         or check_text(r, ["bonk"], tc, "4h2")
         or check_text(r, ["volume"], tc, "4h3")
         or check_text(r, ["count"], tc, "4h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: 1y
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_all_time_trades_single to get WIF ({WIF}) trade stats over 1 year (time_frame=1y)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "1y")
         or check_text(r, ["wif"], tc, "1y2")
         or check_text(r, ["volume"], tc, "1y3")
         or check_text(r, ["year"], tc, "1y4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: ui_amount_mode=raw
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_all_time_trades_single to get BONK ({BONK}) 24h trade stats with raw amounts (ui_amount_mode=raw)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "raw_mode")
         or check_text(r, ["bonk"], tc, "raw_mode2")
         or check_text(r, ["volume"], tc, "raw_mode3")
         or check_text(r, ["count"], tc, "raw_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: chain=ethereum
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_all_time_trades_single to get trade stats for {USDC_ETH} on Ethereum (chain=ethereum, time_frame=24h)",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "eth_chain")
         or check_text(r, ["ethereum"], tc, "eth_chain2")
         or check_text(r, ["volume"], tc, "eth_chain3")
         or check_text(r, ["count"], tc, "eth_chain4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language total trades
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_all_time_trades_single to get the total number of trades for BONK ({BONK}) in the last 24 hours",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "natural_total")
         or check_text(r, ["bonk"], tc, "natural_total2")
         or check_text(r, ["volume"], tc, "natural_total3")
         or check_text(r, ["count"], tc, "natural_total4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language all time volume
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_all_time_trades_single to get the all-time trading volume and trade count for SOL ({SOL})",
        {"birdeye_all_time_trades_single"},
        [lambda r, tc: check_text(r, ["trade"], tc, "natural_alltime")
         or check_text(r, ["sol"], tc, "natural_alltime2")
         or check_text(r, ["volume"], tc, "natural_alltime3")
         or check_text(r, ["all"], tc, "natural_alltime4")],
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

"""
Test suite for GET /defi/price_volume/single via DeFi Query Service LLM.

Coverage:
  TC-01  Basic 24h volume for SOL (default type)
  TC-02  type=1h
  TC-03  type=2h
  TC-04  type=4h
  TC-05  type=8h
  TC-06  BONK 24h volume
  TC-07  WIF 1h volume
  TC-08  ui_amount_mode=scaled
  TC-09  ui_amount_mode=both
  TC-10  Natural language "SOL trading volume today"
  TC-11  Natural language "BONK buy vs sell pressure last hour"
  TC-12  Tool selection: volume query → birdeye_price_volume (not birdeye_price)
  TC-13  JUP 4h volume
  TC-14  RAY 8h volume
  TC-15  type=24h explicit + ui_amount_mode=both combo
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
    print("birdeye_price_volume (GET /defi/price_volume/single) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic 24h (default)
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_price_volume to get SOL ({SOL}) price and volume stats for the last 24 hours",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_24h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: type=1h
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_price_volume to get SOL ({SOL}) price and volume data for type=1h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_1h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: type=2h
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_price_volume to get BONK ({BONK}) price and volume stats for type=2h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_2h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: type=4h
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_price_volume to get WIF ({WIF}) price and volume for type=4h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_4h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: type=8h
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_price_volume to get SOL ({SOL}) price and volume stats for type=8h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_8h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: BONK 24h
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_price_volume to get BONK ({BONK}) 24-hour trading volume and price change",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_24h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: WIF 1h
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_price_volume to get WIF ({WIF}) price and volume data for the last 1 hour type=1h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_1h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_price_volume to get SOL ({SOL}) 24h stats with ui_amount_mode=scaled",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "ui_mode_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: ui_amount_mode=both
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_price_volume to get BONK ({BONK}) 24h price and volume with ui_amount_mode=both",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "ui_mode_both")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "SOL trading volume today"
    results.append(await run_test(
        "TC-10",
        f"What is the SOL ({SOL}) trading volume today? Show 24h price and volume stats.",
        {"birdeye_price_volume", "birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_sol_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "buy vs sell pressure"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_price_volume to check BONK ({BONK}) buy vs sell pressure for the last hour type=1h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "buy_sell_pressure")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: volume query → price_volume not plain price
    results.append(await run_test(
        "TC-12",
        f"What is the trading volume for WIF ({WIF}) over the last 4 hours? Use birdeye_price_volume type=4h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["wif"], tc, "tool_selection_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: JUP 4h
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_price_volume to get JUP ({JUP}) price and 4-hour volume stats type=4h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_4h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: RAY 8h
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_price_volume to get RAY ({RAY}) 8-hour price and volume data type=8h",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_8h_volume")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: type=24h + ui_amount_mode=both combo
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_price_volume for SOL ({SOL}) with type=24h and ui_amount_mode=both",
        {"birdeye_price_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "combo_24h_both")],
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

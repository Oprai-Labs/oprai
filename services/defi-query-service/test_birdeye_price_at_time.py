"""
Test suite for GET /defi/historical_price_unix via DeFi Query Service LLM.

Coverage:
  TC-01  Basic: SOL price at a specific past timestamp
  TC-02  BONK price at a specific past timestamp
  TC-03  WIF price at a past timestamp
  TC-04  unixtime omitted → current price (no unixtime param)
  TC-05  ui_amount_mode=scaled
  TC-06  ui_amount_mode=both
  TC-07  Natural language "what was SOL price on Apr 15 2025"
  TC-08  Natural language "how much was BONK worth last week"
  TC-09  Tool selection: specific date question → birdeye_price_at_time
  TC-10  Tool selection: "price at timestamp X" → birdeye_price_at_time not birdeye_history_price
  TC-11  JUP price at timestamp
  TC-12  RAY price at timestamp
  TC-13  Price 1 week ago vs now comparison
  TC-14  Price at epoch start (beginning of a day)
  TC-15  ui_amount_mode=raw (explicit default)
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"

NOW       = 1745107200   # 2026-04-20 00:00 UTC
WEEK_AGO  = NOW - 7 * 86400
DAY_AGO   = NOW - 86400
APR15     = 1744675200   # 2026-04-15 00:00 UTC

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
    print("birdeye_price_at_time (GET /defi/historical_price_unix) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL price at specific past timestamp
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_price_at_time to get the SOL ({SOL}) price at unixtime={DAY_AGO}",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_at_time")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK price at past timestamp
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_price_at_time to get BONK ({BONK}) price at unixtime={WEEK_AGO}",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_at_time")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF price at past timestamp
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_price_at_time to look up WIF ({WIF}) price at Unix timestamp {APR15}",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_at_time")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: unixtime omitted → current price
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_price_at_time without a unixtime to get the current SOL ({SOL}) price",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "current_price_no_unixtime")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_price_at_time to get SOL ({SOL}) price at unixtime={DAY_AGO} with ui_amount_mode=scaled",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "ui_mode_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=both
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_price_at_time to get BONK ({BONK}) price at unixtime={DAY_AGO} with ui_amount_mode=both",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "ui_mode_both")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Natural language date question
    results.append(await run_test(
        "TC-07",
        f"What was the SOL ({SOL}) price on April 15, 2026? (Unix timestamp for that date is {APR15})",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_date_query")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Natural language "how much was BONK worth last week"
    results.append(await run_test(
        "TC-08",
        f"How much was BONK ({BONK}) worth exactly one week ago? Use birdeye_price_at_time with unixtime={WEEK_AGO}",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_last_week")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Tool selection: specific date question
    results.append(await run_test(
        "TC-09",
        f"What was the exact price of WIF ({WIF}) at Unix timestamp {APR15}?",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["wif"], tc, "tool_selection_exact_time")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Tool selection: "price at timestamp X" vs history
    results.append(await run_test(
        "TC-10",
        f"Give me the single price point for SOL ({SOL}) at exactly {DAY_AGO} (Unix timestamp)",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "single_price_point")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: JUP price at timestamp
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_price_at_time to look up JUP ({JUP}) price at unixtime={WEEK_AGO}",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_at_time")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: RAY price at timestamp
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_price_at_time to get RAY ({RAY}) price at unixtime={DAY_AGO}",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_at_time")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Two price points comparison (call tool twice)
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_price_at_time to get SOL ({SOL}) price at both {WEEK_AGO} and {DAY_AGO} so I can compare",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "two_price_points")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Price at start of a day (epoch boundary)
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_price_at_time to get the SOL ({SOL}) price at the start of April 15, 2026 (unixtime={APR15})",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "epoch_boundary_price")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: explicit ui_amount_mode=raw
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_price_at_time to get SOL ({SOL}) price at unixtime={DAY_AGO} with ui_amount_mode=raw",
        {"birdeye_price_at_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "ui_mode_raw")],
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

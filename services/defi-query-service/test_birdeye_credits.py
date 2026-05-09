"""
Tests for GET /utils/v1/credits via DeFi Query Service LLM.

TC-01  Default (no time range)
TC-02  With time_from only
TC-03  With time_to only
TC-04  With both time_from and time_to
TC-05  Natural language "how many Birdeye API credits do I have left"
TC-06  Natural language "check my API credit balance"
TC-07  Natural language "Birdeye credit consumption this month"
TC-08  Natural language "API usage check"
"""

import asyncio
import httpx
import time

BASE = "http://localhost:3150"

DELAY       = 22
RETRY_DELAY = 38

NOW        = int(time.time())
ONE_DAY    = 86400
WEEK_AGO   = NOW - 7 * ONE_DAY
MONTH_AGO  = NOW - 30 * ONE_DAY


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
    print("birdeye_credits (GET /utils/v1/credits) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Default
    results.append(await run_test(
        "TC-01",
        "Use birdeye_credits to check the current Birdeye API credit balance",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "default")
         or check_text(r, ["api"], tc, "default2")
         or check_text(r, ["quota"], tc, "default3")
         or check_text(r, ["usage"], tc, "default4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: time_from only
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_credits to get API credit usage since {WEEK_AGO} (time_from={WEEK_AGO})",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "time_from")
         or check_text(r, ["api"], tc, "time_from2")
         or check_text(r, ["usage"], tc, "time_from3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: time_to only
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_credits to get API credit usage up to timestamp {NOW} (time_to={NOW})",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "time_to")
         or check_text(r, ["api"], tc, "time_to2")
         or check_text(r, ["usage"], tc, "time_to3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Both time_from and time_to
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_credits to get API credit consumption for the past 30 days (time_from={MONTH_AGO}, time_to={NOW})",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "both_range")
         or check_text(r, ["api"], tc, "both_range2")
         or check_text(r, ["usage"], tc, "both_range3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Natural language credits left
    results.append(await run_test(
        "TC-05",
        "Use birdeye_credits to check how many Birdeye API credits I have left",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "natural_left")
         or check_text(r, ["api"], tc, "natural_left2")
         or check_text(r, ["quota"], tc, "natural_left3")
         or check_text(r, ["remain"], tc, "natural_left4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Natural language credit balance
    results.append(await run_test(
        "TC-06",
        "Use birdeye_credits to check my API credit balance",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "natural_balance")
         or check_text(r, ["api"], tc, "natural_balance2")
         or check_text(r, ["usage"], tc, "natural_balance3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Natural language credit consumption
    results.append(await run_test(
        "TC-07",
        "Use birdeye_credits to show Birdeye credit consumption this month",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "natural_consumption")
         or check_text(r, ["api"], tc, "natural_consumption2")
         or check_text(r, ["usage"], tc, "natural_consumption3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Natural language API usage
    results.append(await run_test(
        "TC-08",
        "Use birdeye_credits to do an API usage check for my Birdeye key",
        {"birdeye_credits"},
        [lambda r, tc: check_text(r, ["credit"], tc, "natural_usage")
         or check_text(r, ["api"], tc, "natural_usage2")
         or check_text(r, ["usage"], tc, "natural_usage3")],
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

"""
Test suite for GET /trader/gainers-losers via DeFi Query Service LLM.

Coverage:
  TC-01  Top gainers this week (default)
  TC-02  Top gainers today
  TC-03  Top gainers yesterday
  TC-04  Top losers this week (sort_type=asc)
  TC-05  Top losers today
  TC-06  Top losers yesterday
  TC-07  limit=5
  TC-08  limit=20
  TC-09  offset=10 (pagination)
  TC-10  chain=solana (explicit)
  TC-11  type=1W + sort_type=desc + limit=5
  TC-12  type=today + sort_type=asc + limit=5
  TC-13  Natural language "who are the top gainers today"
  TC-14  Natural language "biggest losers on Solana this week"
  TC-15  Natural language "best performing traders yesterday"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

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
    print("birdeye_trader_gainers_losers (GET /trader/gainers-losers) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Top gainers this week
    results.append(await run_test(
        "TC-01",
        "Use birdeye_trader_gainers_losers to get the top gaining traders on Solana this week (type=1W, sort_type=desc)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "gainers_1w")
         or check_text(r, ["pnl"], tc, "gainers_1w2")
         or check_text(r, ["profit"], tc, "gainers_1w3")
         or check_text(r, ["gain"], tc, "gainers_1w4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Top gainers today
    results.append(await run_test(
        "TC-02",
        "Use birdeye_trader_gainers_losers to get today's top gaining traders on Solana (type=today, sort_type=desc, limit=10)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "gainers_today")
         or check_text(r, ["pnl"], tc, "gainers_today2")
         or check_text(r, ["profit"], tc, "gainers_today3")
         or check_text(r, ["gain"], tc, "gainers_today4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Top gainers yesterday
    results.append(await run_test(
        "TC-03",
        "Use birdeye_trader_gainers_losers to get yesterday's top gaining traders on Solana (type=yesterday, sort_type=desc, limit=10)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "gainers_yesterday")
         or check_text(r, ["pnl"], tc, "gainers_yesterday2")
         or check_text(r, ["profit"], tc, "gainers_yesterday3")
         or check_text(r, ["gain"], tc, "gainers_yesterday4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Top losers this week
    results.append(await run_test(
        "TC-04",
        "Use birdeye_trader_gainers_losers to get the biggest losing traders on Solana this week (type=1W, sort_type=asc, limit=10)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "losers_1w")
         or check_text(r, ["pnl"], tc, "losers_1w2")
         or check_text(r, ["loss"], tc, "losers_1w3")
         or check_text(r, ["lose"], tc, "losers_1w4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Top losers today
    results.append(await run_test(
        "TC-05",
        "Use birdeye_trader_gainers_losers to get today's biggest losing traders on Solana (type=today, sort_type=asc, limit=10)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "losers_today")
         or check_text(r, ["pnl"], tc, "losers_today2")
         or check_text(r, ["loss"], tc, "losers_today3")
         or check_text(r, ["lose"], tc, "losers_today4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Top losers yesterday
    results.append(await run_test(
        "TC-06",
        "Use birdeye_trader_gainers_losers to get yesterday's biggest losing traders on Solana (type=yesterday, sort_type=asc, limit=10)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "losers_yesterday")
         or check_text(r, ["pnl"], tc, "losers_yesterday2")
         or check_text(r, ["loss"], tc, "losers_yesterday3")
         or check_text(r, ["lose"], tc, "losers_yesterday4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: limit=5
    results.append(await run_test(
        "TC-07",
        "Use birdeye_trader_gainers_losers to get the top 5 gainers on Solana this week (type=1W, limit=5)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "limit5")
         or check_text(r, ["pnl"], tc, "limit5b")
         or check_text(r, ["profit"], tc, "limit5c")
         or check_text(r, ["gain"], tc, "limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: limit=20
    results.append(await run_test(
        "TC-08",
        "Use birdeye_trader_gainers_losers to get the top 20 gaining traders this week (type=1W, sort_type=desc, limit=20)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "limit20")
         or check_text(r, ["pnl"], tc, "limit20b")
         or check_text(r, ["profit"], tc, "limit20c")
         or check_text(r, ["gain"], tc, "limit20d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: offset pagination
    results.append(await run_test(
        "TC-09",
        "Use birdeye_trader_gainers_losers to get the next page of top gainers this week (type=1W, offset=10, limit=10)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "offset_page")
         or check_text(r, ["pnl"], tc, "offset_page2")
         or check_text(r, ["profit"], tc, "offset_page3")
         or check_text(r, ["gain"], tc, "offset_page4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: chain=solana explicit
    results.append(await run_test(
        "TC-10",
        "Use birdeye_trader_gainers_losers to get the top gaining Solana traders this week (chain=solana, type=1W, limit=10)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "chain_sol")
         or check_text(r, ["pnl"], tc, "chain_sol2")
         or check_text(r, ["profit"], tc, "chain_sol3")
         or check_text(r, ["gain"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: 1W gainers top 5
    results.append(await run_test(
        "TC-11",
        "Use birdeye_trader_gainers_losers to get the top 5 Solana traders by PnL this week (type=1W, sort_type=desc, sort_by=PnL, limit=5)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "1w_top5")
         or check_text(r, ["pnl"], tc, "1w_top5b")
         or check_text(r, ["profit"], tc, "1w_top5c")
         or check_text(r, ["gain"], tc, "1w_top5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: today losers top 5
    results.append(await run_test(
        "TC-12",
        "Use birdeye_trader_gainers_losers to get today's top 5 losing traders on Solana (type=today, sort_type=asc, limit=5)",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "today_losers5")
         or check_text(r, ["pnl"], tc, "today_losers5b")
         or check_text(r, ["loss"], tc, "today_losers5c")
         or check_text(r, ["lose"], tc, "today_losers5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "top gainers today"
    results.append(await run_test(
        "TC-13",
        "Use birdeye_trader_gainers_losers to show me who the top gaining traders on Solana are today",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "natural_gainers")
         or check_text(r, ["pnl"], tc, "natural_gainers2")
         or check_text(r, ["profit"], tc, "natural_gainers3")
         or check_text(r, ["gain"], tc, "natural_gainers4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "biggest losers this week"
    results.append(await run_test(
        "TC-14",
        "Use birdeye_trader_gainers_losers to show me the biggest losing traders on Solana DeFi this week",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "natural_losers")
         or check_text(r, ["pnl"], tc, "natural_losers2")
         or check_text(r, ["loss"], tc, "natural_losers3")
         or check_text(r, ["lose"], tc, "natural_losers4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language "best traders yesterday"
    results.append(await run_test(
        "TC-15",
        "Use birdeye_trader_gainers_losers to tell me which traders had the best performance on Solana yesterday",
        {"birdeye_trader_gainers_losers"},
        [lambda r, tc: check_text(r, ["trader"], tc, "natural_yesterday")
         or check_text(r, ["pnl"], tc, "natural_yesterday2")
         or check_text(r, ["profit"], tc, "natural_yesterday3")
         or check_text(r, ["gain"], tc, "natural_yesterday4")],
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

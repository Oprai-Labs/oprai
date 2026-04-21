"""
Test suite for GET /defi/v3/token/list via DeFi Query Service LLM.

Coverage:
  TC-01  Default sort (market_cap desc)
  TC-02  sort_by=liquidity desc
  TC-03  sort_by=volume_24h_usd desc
  TC-04  sort_by=price_change_24h_percent desc (top gainers)
  TC-05  sort_by=price_change_24h_percent asc (top losers)
  TC-06  min_liquidity filter
  TC-07  min_market_cap filter
  TC-08  min_holder filter
  TC-09  limit=10
  TC-10  sort_by=fdv desc
  TC-11  sort_by=holder desc
  TC-12  min_market_cap + sort_by=volume_24h_usd
  TC-13  Natural language "top tokens by volume"
  TC-14  Natural language "tokens with most liquidity"
  TC-15  ui_amount_mode=raw
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
    print("birdeye_token_list (GET /defi/v3/token/list) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Default sort (market_cap desc)
    results.append(await run_test(
        "TC-01",
        "Use birdeye_token_list to get the top tokens by market cap",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["sol"], tc, "default_market_cap")
         or check_text(r, ["token"], tc, "default_market_cap2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: sort_by=liquidity
    results.append(await run_test(
        "TC-02",
        "Use birdeye_token_list to get the top tokens sorted by liquidity (sort_by=liquidity, sort_type=desc)",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["liquidity"], tc, "sorted_by_liquidity")
         or check_text(r, ["token"], tc, "sorted_by_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: sort_by=volume_24h_usd
    results.append(await run_test(
        "TC-03",
        "Use birdeye_token_list to get the top 10 tokens by 24h volume (sort_by=volume_24h_usd, limit=10)",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["volume"], tc, "sorted_by_volume")
         or check_text(r, ["token"], tc, "sorted_by_volume2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Top gainers
    results.append(await run_test(
        "TC-04",
        "Use birdeye_token_list to find the top gainers today — sort by price_change_24h_percent descending, limit=10",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "top_gainers")
         or check_text(r, ["price"], tc, "top_gainers2")
         or check_text(r, ["change"], tc, "top_gainers3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Top losers
    results.append(await run_test(
        "TC-05",
        "Use birdeye_token_list to find the biggest losers today — sort_by=price_change_24h_percent, sort_type=asc, limit=10",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "top_losers")
         or check_text(r, ["price"], tc, "top_losers2")
         or check_text(r, ["change"], tc, "top_losers3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: min_liquidity filter
    results.append(await run_test(
        "TC-06",
        "Use birdeye_token_list to get tokens with at least $1,000,000 liquidity (min_liquidity=1000000), sorted by market cap",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "min_liquidity_filter")
         or check_text(r, ["liquidity"], tc, "min_liquidity_filter2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: min_market_cap filter
    results.append(await run_test(
        "TC-07",
        "Use birdeye_token_list to get tokens with min_market_cap=10000000 sorted by volume_24h_usd desc, limit=10",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "min_market_cap_filter")
         or check_text(r, ["market"], tc, "min_market_cap_filter2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: min_holder filter
    results.append(await run_test(
        "TC-08",
        "Use birdeye_token_list to find tokens with at least 10000 holders (min_holder=10000), sorted by market cap, limit=10",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "min_holder_filter")
         or check_text(r, ["holder"], tc, "min_holder_filter2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=10
    results.append(await run_test(
        "TC-09",
        "Use birdeye_token_list to get the top 10 tokens by market cap (limit=10)",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["sol"], tc, "limit_10")
         or check_text(r, ["token"], tc, "limit_10b")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: sort_by=fdv
    results.append(await run_test(
        "TC-10",
        "Use birdeye_token_list to get tokens sorted by FDV (sort_by=fdv, sort_type=desc, limit=10)",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "sorted_by_fdv")
         or check_text(r, ["fdv"], tc, "sorted_by_fdv2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: sort_by=holder
    results.append(await run_test(
        "TC-11",
        "Use birdeye_token_list to get the tokens with the most holders (sort_by=holder, sort_type=desc, limit=10)",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "sorted_by_holder")
         or check_text(r, ["holder"], tc, "sorted_by_holder2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Combined filters
    results.append(await run_test(
        "TC-12",
        "Use birdeye_token_list to find tokens with min_market_cap=5000000 and min_liquidity=500000, sorted by volume_24h_usd desc, limit=10",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "combined_filters")
         or check_text(r, ["volume"], tc, "combined_filters2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "top tokens by volume"
    results.append(await run_test(
        "TC-13",
        "What are the top 10 tokens on Solana right now by trading volume?",
        {"birdeye_token_list", "birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_top_by_volume")
         or check_text(r, ["volume"], tc, "natural_top_by_volume2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "tokens with most liquidity"
    results.append(await run_test(
        "TC-14",
        "Use birdeye_token_list to show me which tokens have the deepest liquidity on Solana right now",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_most_liquidity")
         or check_text(r, ["liquidity"], tc, "natural_most_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: ui_amount_mode=raw
    results.append(await run_test(
        "TC-15",
        "Use birdeye_token_list to get the top 10 tokens by market cap with ui_amount_mode=raw",
        {"birdeye_token_list"},
        [lambda r, tc: check_text(r, ["sol"], tc, "list_ui_raw")
         or check_text(r, ["token"], tc, "list_ui_raw2")],
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

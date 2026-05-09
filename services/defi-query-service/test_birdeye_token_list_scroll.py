"""
Test suite for GET /defi/v3/token/list/scroll via DeFi Query Service LLM.

Coverage:
  TC-01  Basic scroll (market_cap desc, no filters)
  TC-02  sort_by=volume_24h_usd with min_volume_24h_usd filter
  TC-03  sort_by=liquidity with min_liquidity filter
  TC-04  min_market_cap filter
  TC-05  min_holder filter
  TC-06  sort_by=price_change_24h_percent (top gainers large set)
  TC-07  min_price_change_24h_percent filter (only positive movers)
  TC-08  min_trade_24h_count filter (active traders)
  TC-09  limit=50
  TC-10  sort_by=fdv with min_fdv filter
  TC-11  Combined: min_liquidity + min_volume + sort_by=volume
  TC-12  Natural language "large list of tokens sorted by volume"
  TC-13  Natural language "scroll through all tokens"
  TC-14  ui_amount_mode=raw
  TC-15  sort_by=holder desc with min_holder filter
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
    print("birdeye_token_list_scroll (GET /defi/v3/token/list/scroll) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic scroll
    results.append(await run_test(
        "TC-01",
        "Use birdeye_token_list_scroll to get a large list of tokens sorted by market cap",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "basic_scroll")
         or check_text(r, ["sol"], tc, "basic_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: volume filter
    results.append(await run_test(
        "TC-02",
        "Use birdeye_token_list_scroll to get tokens with min_volume_24h_usd=100000, sorted by volume_24h_usd desc, limit=50",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "volume_filter_scroll")
         or check_text(r, ["volume"], tc, "volume_filter_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: liquidity filter
    results.append(await run_test(
        "TC-03",
        "Use birdeye_token_list_scroll to get tokens with min_liquidity=500000, sorted by liquidity desc, limit=50",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "liquidity_filter_scroll")
         or check_text(r, ["liquidity"], tc, "liquidity_filter_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: min_market_cap
    results.append(await run_test(
        "TC-04",
        "Use birdeye_token_list_scroll to get tokens with min_market_cap=1000000, sorted by market_cap desc",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "market_cap_scroll")
         or check_text(r, ["market"], tc, "market_cap_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: min_holder
    results.append(await run_test(
        "TC-05",
        "Use birdeye_token_list_scroll to get tokens with min_holder=5000, sorted by holder desc, limit=50",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "holder_scroll")
         or check_text(r, ["holder"], tc, "holder_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Top gainers large set
    results.append(await run_test(
        "TC-06",
        "Use birdeye_token_list_scroll to get a large list of top gaining tokens — sort_by=price_change_24h_percent, sort_type=desc, limit=100",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "gainers_scroll")
         or check_text(r, ["price"], tc, "gainers_scroll2")
         or check_text(r, ["change"], tc, "gainers_scroll3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: min_price_change filter
    results.append(await run_test(
        "TC-07",
        "Use birdeye_token_list_scroll to get tokens with min_price_change_24h_percent=5, sorted by price_change_24h_percent desc",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "price_change_filter")
         or check_text(r, ["price"], tc, "price_change_filter2")
         or check_text(r, ["change"], tc, "price_change_filter3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: min_trade_24h_count
    results.append(await run_test(
        "TC-08",
        "Use birdeye_token_list_scroll to find actively traded tokens with min_trade_24h_count=1000, sorted by volume_24h_usd desc",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "trade_count_scroll")
         or check_text(r, ["trade"], tc, "trade_count_scroll2")
         or check_text(r, ["volume"], tc, "trade_count_scroll3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=50
    results.append(await run_test(
        "TC-09",
        "Use birdeye_token_list_scroll to get 50 tokens sorted by market cap",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["sol"], tc, "limit_50_scroll")
         or check_text(r, ["token"], tc, "limit_50_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: FDV filter
    results.append(await run_test(
        "TC-10",
        "Use birdeye_token_list_scroll to get tokens with min_fdv=50000000 sorted by fdv desc",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "fdv_scroll")
         or check_text(r, ["fdv"], tc, "fdv_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Combined filters
    results.append(await run_test(
        "TC-11",
        "Use birdeye_token_list_scroll to find liquid, high-volume tokens: min_liquidity=1000000, min_volume_24h_usd=500000, sort_by=volume_24h_usd, limit=50",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "combined_scroll_filters")
         or check_text(r, ["volume"], tc, "combined_scroll_filters2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "large list"
    results.append(await run_test(
        "TC-12",
        "Use birdeye_token_list_scroll to give me a large batch of tokens sorted by 24h volume",
        {"birdeye_token_list_scroll", "birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_large_list")
         or check_text(r, ["volume"], tc, "natural_large_list2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "scroll"
    results.append(await run_test(
        "TC-13",
        "Use birdeye_token_list_scroll to paginate through Solana tokens with min_market_cap=500000",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_scroll")
         or check_text(r, ["market"], tc, "natural_scroll2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: ui_amount_mode=raw
    results.append(await run_test(
        "TC-14",
        "Use birdeye_token_list_scroll to get 50 tokens by market cap with ui_amount_mode=raw",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "scroll_ui_raw")
         or check_text(r, ["sol"], tc, "scroll_ui_raw2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: holder + min_holder
    results.append(await run_test(
        "TC-15",
        "Use birdeye_token_list_scroll to find tokens with the most holders: min_holder=10000, sort_by=holder, sort_type=desc",
        {"birdeye_token_list_scroll"},
        [lambda r, tc: check_text(r, ["token"], tc, "holder_sort_scroll")
         or check_text(r, ["holder"], tc, "holder_sort_scroll2")],
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

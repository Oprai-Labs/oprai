"""
Test suite for GET /defi/tokenlist via DeFi Query Service LLM.

Coverage:
  TC-01  Default (v24hUSD desc)
  TC-02  sort_by=mc desc (market cap)
  TC-03  sort_by=liquidity desc
  TC-04  sort_by=v24hChangePercent desc (volume gainers)
  TC-05  sort_by=v24hChangePercent asc (volume losers)
  TC-06  sort_type=asc with sort_by=v24hUSD
  TC-07  limit=10
  TC-08  min_liquidity=10000
  TC-09  min_liquidity=1000000 (high liquidity filter)
  TC-10  max_liquidity=50000 (small-cap filter)
  TC-11  offset=50 (pagination)
  TC-12  Natural language "trending tokens by volume"
  TC-13  Natural language "biggest tokens by market cap"
  TC-14  ui_amount_mode=raw
  TC-15  sort_by=mc + min_liquidity combo
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
    print("birdeye_tokenlist_v1 (GET /defi/tokenlist) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Default (v24hUSD desc)
    results.append(await run_test(
        "TC-01",
        "Use birdeye_tokenlist_v1 to get the top tokens by 24h volume",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "default_v24h")
         or check_text(r, ["sol"], tc, "default_v24h2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: sort_by=mc
    results.append(await run_test(
        "TC-02",
        "Use birdeye_tokenlist_v1 to get the top tokens by market cap (sort_by=mc, sort_type=desc)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "sort_by_mc")
         or check_text(r, ["market"], tc, "sort_by_mc2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: sort_by=liquidity
    results.append(await run_test(
        "TC-03",
        "Use birdeye_tokenlist_v1 to get the most liquid tokens (sort_by=liquidity, sort_type=desc)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "sort_by_liquidity")
         or check_text(r, ["liquidity"], tc, "sort_by_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: sort_by=v24hChangePercent desc (volume gainers)
    results.append(await run_test(
        "TC-04",
        "Use birdeye_tokenlist_v1 to find tokens with the biggest volume increase today (sort_by=v24hChangePercent, sort_type=desc)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "volume_gainers")
         or check_text(r, ["volume"], tc, "volume_gainers2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: sort_by=v24hChangePercent asc
    results.append(await run_test(
        "TC-05",
        "Use birdeye_tokenlist_v1 to find tokens with the biggest volume decline today (sort_by=v24hChangePercent, sort_type=asc)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "volume_losers")
         or check_text(r, ["volume"], tc, "volume_losers2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: sort_type=asc
    results.append(await run_test(
        "TC-06",
        "Use birdeye_tokenlist_v1 to get tokens with the lowest 24h volume (sort_by=v24hUSD, sort_type=asc)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "lowest_volume")
         or check_text(r, ["volume"], tc, "lowest_volume2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: limit=10
    results.append(await run_test(
        "TC-07",
        "Use birdeye_tokenlist_v1 to get the top 10 tokens by 24h volume (limit=10)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit_10_v1")
         or check_text(r, ["sol"], tc, "limit_10_v1b")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: min_liquidity=10000
    results.append(await run_test(
        "TC-08",
        "Use birdeye_tokenlist_v1 to get tokens with at least $10,000 liquidity (min_liquidity=10000)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "min_liq_10k")
         or check_text(r, ["liquidity"], tc, "min_liq_10k2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: min_liquidity=1000000
    results.append(await run_test(
        "TC-09",
        "Use birdeye_tokenlist_v1 to get tokens with at least $1M liquidity (min_liquidity=1000000)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "min_liq_1m")
         or check_text(r, ["liquidity"], tc, "min_liq_1m2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: max_liquidity filter
    results.append(await run_test(
        "TC-10",
        "Use birdeye_tokenlist_v1 to get small-cap tokens with max_liquidity=50000, sorted by v24hUSD",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "max_liquidity_filter")
         or check_text(r, ["liquidity"], tc, "max_liquidity_filter2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: offset=50
    results.append(await run_test(
        "TC-11",
        "Use birdeye_tokenlist_v1 to get the next page of tokens starting at offset=50",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "offset_50")
         or check_text(r, ["volume"], tc, "offset_50b")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "trending"
    results.append(await run_test(
        "TC-12",
        "Use birdeye_tokenlist_v1 to show me the trending tokens by volume on Solana",
        {"birdeye_tokenlist_v1", "birdeye_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_trending")
         or check_text(r, ["volume"], tc, "natural_trending2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "biggest tokens"
    results.append(await run_test(
        "TC-13",
        "Use birdeye_tokenlist_v1 to list the biggest tokens on Solana by market cap",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_biggest")
         or check_text(r, ["sol"], tc, "natural_biggest2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: ui_amount_mode=raw
    results.append(await run_test(
        "TC-14",
        "Use birdeye_tokenlist_v1 to get the top tokens by volume with ui_amount_mode=raw",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "v1_ui_raw")
         or check_text(r, ["sol"], tc, "v1_ui_raw2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Combined sort_by=mc + min_liquidity
    results.append(await run_test(
        "TC-15",
        "Use birdeye_tokenlist_v1 to get the biggest tokens by market cap with min_liquidity=500000 (sort_by=mc, limit=20)",
        {"birdeye_tokenlist_v1"},
        [lambda r, tc: check_text(r, ["token"], tc, "mc_min_liq_combo")
         or check_text(r, ["market"], tc, "mc_min_liq_combo2")],
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

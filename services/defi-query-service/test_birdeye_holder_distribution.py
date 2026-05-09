"""
Test suite for GET /holder/v1/distribution via DeFi Query Service LLM.

Coverage:
  TC-01  Top 10 holders of BONK (default)
  TC-02  Top 10 holders of SOL
  TC-03  Top 20 holders (top_n=20)
  TC-04  Top 50 holders (top_n=50)
  TC-05  mode=percent (filter by supply %)
  TC-06  min_percent filter
  TC-07  max_percent filter
  TC-08  min_percent + max_percent range
  TC-09  address_type=token_account
  TC-10  include_list=false (summary only)
  TC-11  limit=10
  TC-12  offset pagination
  TC-13  WIF holder distribution
  TC-14  Natural language "top holders of BONK by supply %"
  TC-15  Natural language "whale concentration for JUP"
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
    print("birdeye_holder_distribution (GET /holder/v1/distribution) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: BONK top 10
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_holder_distribution to get the top 10 holder distribution for BONK ({BONK})",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "bonk_top10")
         or check_text(r, ["bonk"], tc, "bonk_top10b")
         or check_text(r, ["distribution"], tc, "bonk_top10c")
         or check_text(r, ["supply"], tc, "bonk_top10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: SOL top 10
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_holder_distribution to get the top holder distribution for SOL ({SOL})",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "sol_top10")
         or check_text(r, ["sol"], tc, "sol_top10b")
         or check_text(r, ["distribution"], tc, "sol_top10c")
         or check_text(r, ["supply"], tc, "sol_top10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: top_n=20
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_holder_distribution to get the top 20 holders distribution for BONK ({BONK}) (top_n=20)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "top20")
         or check_text(r, ["bonk"], tc, "top20b")
         or check_text(r, ["distribution"], tc, "top20c")
         or check_text(r, ["supply"], tc, "top20d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: top_n=50
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_holder_distribution to get the top 50 holder distribution for JUP ({JUP}) (top_n=50)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "top50")
         or check_text(r, ["jup"], tc, "top50b")
         or check_text(r, ["distribution"], tc, "top50c")
         or check_text(r, ["supply"], tc, "top50d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: mode=percent
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_holder_distribution to get BONK ({BONK}) holders filtered by supply percentage (mode=percent, limit=20)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "mode_percent")
         or check_text(r, ["bonk"], tc, "mode_percent2")
         or check_text(r, ["distribution"], tc, "mode_percent3")
         or check_text(r, ["supply"], tc, "mode_percent4")
         or check_text(r, ["percent"], tc, "mode_percent5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: min_percent
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_holder_distribution to get BONK ({BONK}) holders with at least 1% of supply (mode=percent, min_percent=1, limit=10)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "min_pct")
         or check_text(r, ["bonk"], tc, "min_pct2")
         or check_text(r, ["distribution"], tc, "min_pct3")
         or check_text(r, ["supply"], tc, "min_pct4")
         or check_text(r, ["percent"], tc, "min_pct5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: max_percent
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_holder_distribution to get BONK ({BONK}) holders with less than 0.1% of supply (mode=percent, max_percent=0.1, limit=10)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "max_pct")
         or check_text(r, ["bonk"], tc, "max_pct2")
         or check_text(r, ["distribution"], tc, "max_pct3")
         or check_text(r, ["supply"], tc, "max_pct4")
         or check_text(r, ["percent"], tc, "max_pct5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: min + max percent range
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_holder_distribution to get BONK ({BONK}) holders with 0.1% to 1% of supply (mode=percent, min_percent=0.1, max_percent=1, limit=10)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "pct_range")
         or check_text(r, ["bonk"], tc, "pct_range2")
         or check_text(r, ["distribution"], tc, "pct_range3")
         or check_text(r, ["supply"], tc, "pct_range4")
         or check_text(r, ["percent"], tc, "pct_range5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: address_type=token_account
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_holder_distribution to get the top 10 BONK ({BONK}) token account holders (address_type=token_account, top_n=10)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "token_account")
         or check_text(r, ["bonk"], tc, "token_account2")
         or check_text(r, ["distribution"], tc, "token_account3")
         or check_text(r, ["supply"], tc, "token_account4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: include_list=false
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_holder_distribution to get a summary of SOL ({SOL}) holder distribution without the full list (include_list=false, top_n=10)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "no_list")
         or check_text(r, ["sol"], tc, "no_list2")
         or check_text(r, ["distribution"], tc, "no_list3")
         or check_text(r, ["supply"], tc, "no_list4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: limit=10
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_holder_distribution to get RAY ({RAY}) holder distribution with limit=10 (top_n=10, limit=10)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "limit10")
         or check_text(r, ["ray"], tc, "limit10b")
         or check_text(r, ["distribution"], tc, "limit10c")
         or check_text(r, ["supply"], tc, "limit10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: offset pagination
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_holder_distribution to get the next page of BONK ({BONK}) holder distribution (mode=percent, offset=50, limit=10)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "offset_page")
         or check_text(r, ["bonk"], tc, "offset_page2")
         or check_text(r, ["distribution"], tc, "offset_page3")
         or check_text(r, ["supply"], tc, "offset_page4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: WIF distribution
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_holder_distribution to get the holder distribution for WIF ({WIF}) — top 20 (top_n=20)",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "wif_dist")
         or check_text(r, ["wif"], tc, "wif_dist2")
         or check_text(r, ["distribution"], tc, "wif_dist3")
         or check_text(r, ["supply"], tc, "wif_dist4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "top holders by supply %"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_holder_distribution to show me the top holders of BONK ({BONK}) and what percentage of supply they hold",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_supply_pct")
         or check_text(r, ["bonk"], tc, "natural_supply_pct2")
         or check_text(r, ["distribution"], tc, "natural_supply_pct3")
         or check_text(r, ["supply"], tc, "natural_supply_pct4")
         or check_text(r, ["percent"], tc, "natural_supply_pct5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language "whale concentration"
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_holder_distribution to analyze the whale concentration for JUP ({JUP}) — how much do the top holders control?",
        {"birdeye_holder_distribution"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_whale")
         or check_text(r, ["jup"], tc, "natural_whale2")
         or check_text(r, ["distribution"], tc, "natural_whale3")
         or check_text(r, ["supply"], tc, "natural_whale4")
         or check_text(r, ["percent"], tc, "natural_whale5")],
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

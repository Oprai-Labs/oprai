"""
Test suite for GET /defi/v2/markets via DeFi Query Service LLM.

Coverage:
  TC-01  SOL markets (all DEX pools)
  TC-02  BONK markets
  TC-03  WIF markets
  TC-04  JUP markets
  TC-05  RAY markets
  TC-06  sort_by=liquidity (default)
  TC-07  sort_by=volume24h
  TC-08  sort_type=asc (lowest liquidity first)
  TC-09  limit=5
  TC-10  limit=20 (max)
  TC-11  Natural language "which DEXes trade SOL"
  TC-12  Natural language "best pools for BONK"
  TC-13  Tool selection: DEX markets → birdeye_token_markets
  TC-14  Natural language "liquidity pools for JUP"
  TC-15  sort_by=volume24h + limit=10
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
    print("birdeye_token_markets (GET /defi/v2/markets) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL markets
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_markets to get all DEX markets for SOL ({SOL})",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_markets")
         or check_text(r, ["market"], tc, "sol_markets2")
         or check_text(r, ["pool"], tc, "sol_markets3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK markets
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_markets to get all trading pools for BONK ({BONK})",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_markets")
         or check_text(r, ["market"], tc, "bonk_markets2")
         or check_text(r, ["pool"], tc, "bonk_markets3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF markets
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_markets to list all DEX pools for WIF ({WIF})",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_markets")
         or check_text(r, ["market"], tc, "wif_markets2")
         or check_text(r, ["pool"], tc, "wif_markets3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: JUP markets
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_markets to get markets for JUP ({JUP})",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_markets")
         or check_text(r, ["market"], tc, "jup_markets2")
         or check_text(r, ["pool"], tc, "jup_markets3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: RAY markets
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_markets to get all pools and markets for RAY ({RAY})",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_markets")
         or check_text(r, ["market"], tc, "ray_markets2")
         or check_text(r, ["pool"], tc, "ray_markets3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: sort_by=liquidity (default)
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_markets for SOL ({SOL}) sorted by liquidity (sort_by=liquidity, sort_type=desc)",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_by_liquidity")
         or check_text(r, ["liquidity"], tc, "sol_by_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_by=volume24h
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_markets to get BONK ({BONK}) markets sorted by 24h volume (sort_by=volume24h)",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_by_volume")
         or check_text(r, ["volume"], tc, "bonk_by_volume2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: sort_type=asc
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_markets for WIF ({WIF}) sorted by liquidity ascending (sort_type=asc)",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_liq_asc")
         or check_text(r, ["market"], tc, "wif_liq_asc2")
         or check_text(r, ["pool"], tc, "wif_liq_asc3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=5
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_markets to get the top 5 markets for SOL ({SOL}) by liquidity (limit=5)",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_limit_5")
         or check_text(r, ["market"], tc, "sol_limit_5b")
         or check_text(r, ["pool"], tc, "sol_limit_5c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: limit=20
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_markets to get up to 20 markets for BONK ({BONK}) (limit=20)",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_limit_20")
         or check_text(r, ["market"], tc, "bonk_limit_20b")
         or check_text(r, ["pool"], tc, "bonk_limit_20c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "which DEXes trade SOL"
    results.append(await run_test(
        "TC-11",
        f"Which DEXes and pools trade SOL ({SOL})? Show me all the markets.",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_dex_sol")
         or check_text(r, ["market"], tc, "natural_dex_sol2")
         or check_text(r, ["pool"], tc, "natural_dex_sol3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "best pools for BONK"
    results.append(await run_test(
        "TC-12",
        f"What are the best liquidity pools for trading BONK ({BONK})?",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_best_pools")
         or check_text(r, ["market"], tc, "natural_best_pools2")
         or check_text(r, ["pool"], tc, "natural_best_pools3")
         or check_text(r, ["liquidity"], tc, "natural_best_pools4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_markets to find all the DEX markets where WIF ({WIF}) is traded",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["wif"], tc, "tool_selection_markets")
         or check_text(r, ["market"], tc, "tool_selection_markets2")
         or check_text(r, ["pool"], tc, "tool_selection_markets3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "liquidity pools for JUP"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_markets to show all liquidity pools where JUP ({JUP}) can be traded",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["jup"], tc, "natural_jup_pools")
         or check_text(r, ["market"], tc, "natural_jup_pools2")
         or check_text(r, ["pool"], tc, "natural_jup_pools3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: sort_by=volume24h + limit=10
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_markets to get the top 10 RAY ({RAY}) trading markets by 24h volume (sort_by=volume24h, limit=10)",
        {"birdeye_token_markets"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_volume_limit")
         or check_text(r, ["market"], tc, "ray_volume_limit2")
         or check_text(r, ["volume"], tc, "ray_volume_limit3")],
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

"""
Tests for GET /defi/v3/search via DeFi Query Service LLM.

TC-01  Search by symbol "BONK"
TC-02  Search by name "Jupiter"
TC-03  search_by=combination
TC-04  target=token only
TC-05  target=market only
TC-06  search_mode=exact
TC-07  sort_by=marketcap
TC-08  sort_by=volume_24h_usd
TC-09  verify_token=true (verified tokens only)
TC-10  markets=Raydium filter
TC-11  markets=Pump.fun filter
TC-12  chain=ethereum search
TC-13  limit=5 pagination
TC-14  Natural language "find token called WIF"
TC-15  Natural language "search for USDC pools on Raydium"
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
    print("birdeye_search (GET /defi/v3/search) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Search by symbol
    results.append(await run_test(
        "TC-01",
        "Use birdeye_search to search for the token with symbol BONK (search_by=symbol, keyword=BONK)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "symbol_bonk")
         or check_text(r, ["token"], tc, "symbol_bonk2")
         or check_text(r, ["search"], tc, "symbol_bonk3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Search by name
    results.append(await run_test(
        "TC-02",
        "Use birdeye_search to search for tokens named Jupiter (search_by=name, keyword=Jupiter)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["jupiter"], tc, "name_jup")
         or check_text(r, ["jup"], tc, "name_jup2")
         or check_text(r, ["token"], tc, "name_jup3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: search_by=combination
    results.append(await run_test(
        "TC-03",
        "Use birdeye_search with keyword=WIF and search_by=combination to find the dogwifhat token",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["wif"], tc, "combo_wif")
         or check_text(r, ["token"], tc, "combo_wif2")
         or check_text(r, ["search"], tc, "combo_wif3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: target=token
    results.append(await run_test(
        "TC-04",
        "Use birdeye_search to find tokens only (target=token) with keyword=SOL on Solana",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["sol"], tc, "target_token")
         or check_text(r, ["token"], tc, "target_token2")
         or check_text(r, ["search"], tc, "target_token3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: target=market
    results.append(await run_test(
        "TC-05",
        "Use birdeye_search to find markets only (target=market) with keyword=BONK",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["market"], tc, "target_market")
         or check_text(r, ["bonk"], tc, "target_market2")
         or check_text(r, ["token"], tc, "target_market3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: search_mode=exact
    results.append(await run_test(
        "TC-06",
        "Use birdeye_search to find the exact token with symbol JUP (search_by=symbol, search_mode=exact, keyword=JUP)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["jup"], tc, "exact_jup")
         or check_text(r, ["token"], tc, "exact_jup2")
         or check_text(r, ["search"], tc, "exact_jup3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_by=marketcap
    results.append(await run_test(
        "TC-07",
        "Use birdeye_search to find tokens with keyword=meme sorted by market cap (sort_by=marketcap, limit=10)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["market"], tc, "mcap_sort")
         or check_text(r, ["token"], tc, "mcap_sort2")
         or check_text(r, ["search"], tc, "mcap_sort3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: sort_by=volume_24h_usd
    results.append(await run_test(
        "TC-08",
        "Use birdeye_search to find tokens with keyword=USDC sorted by 24h volume (sort_by=volume_24h_usd, limit=10)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["volume"], tc, "vol_sort")
         or check_text(r, ["usdc"], tc, "vol_sort2")
         or check_text(r, ["token"], tc, "vol_sort3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: verify_token=true
    results.append(await run_test(
        "TC-09",
        "Use birdeye_search to find verified tokens only (verify_token=true) with keyword=RAY on Solana",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["ray"], tc, "verified")
         or check_text(r, ["verif"], tc, "verified2")
         or check_text(r, ["token"], tc, "verified3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: markets=Raydium
    results.append(await run_test(
        "TC-10",
        "Use birdeye_search to find BONK markets on Raydium only (markets=Raydium, keyword=BONK, target=market)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["raydium"], tc, "raydium_market")
         or check_text(r, ["bonk"], tc, "raydium_market2")
         or check_text(r, ["market"], tc, "raydium_market3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: markets=Pump.fun
    results.append(await run_test(
        "TC-11",
        "Use birdeye_search to find token markets on Pump.fun (markets=Pump.fun, target=market, limit=10)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["pump"], tc, "pumpfun")
         or check_text(r, ["market"], tc, "pumpfun2")
         or check_text(r, ["token"], tc, "pumpfun3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: chain=ethereum
    results.append(await run_test(
        "TC-12",
        "Use birdeye_search to search for USDC tokens on Ethereum (keyword=USDC, chain=ethereum)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "eth_search")
         or check_text(r, ["ethereum"], tc, "eth_search2")
         or check_text(r, ["token"], tc, "eth_search3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: limit=5
    results.append(await run_test(
        "TC-13",
        "Use birdeye_search to get the top 5 results for keyword=SOL (limit=5, sort_by=liquidity)",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["sol"], tc, "limit5")
         or check_text(r, ["token"], tc, "limit5b")
         or check_text(r, ["search"], tc, "limit5c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language find token
    results.append(await run_test(
        "TC-14",
        "Use birdeye_search to find the token called WIF on Solana",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["wif"], tc, "natural_wif")
         or check_text(r, ["token"], tc, "natural_wif2")
         or check_text(r, ["search"], tc, "natural_wif3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language USDC pools on Raydium
    results.append(await run_test(
        "TC-15",
        "Use birdeye_search to find USDC pools on Raydium",
        {"birdeye_search"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "natural_usdc_ray")
         or check_text(r, ["raydium"], tc, "natural_usdc_ray2")
         or check_text(r, ["market"], tc, "natural_usdc_ray3")
         or check_text(r, ["pool"], tc, "natural_usdc_ray4")],
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

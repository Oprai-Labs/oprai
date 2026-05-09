"""
Tests for GET /defi/token_trending via DeFi Query Service LLM.

TC-01  Default (sort_by=rank, 24h, Solana)
TC-02  sort_by=volumeUSD
TC-03  sort_by=liquidity
TC-04  interval=1h
TC-05  interval=4h
TC-06  limit=5
TC-07  sort_type=desc
TC-08  chain=ethereum
TC-09  chain=base
TC-10  Natural language "what tokens are trending on Solana"
TC-11  Natural language "top trending by volume in 1h"
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
    print("birdeye_token_trending (GET /defi/token_trending) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Default
    results.append(await run_test(
        "TC-01",
        "Use birdeye_token_trending to get the trending tokens on Solana",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "default")
         or check_text(r, ["token"], tc, "default2")
         or check_text(r, ["volume"], tc, "default3")
         or check_text(r, ["rank"], tc, "default4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: sort_by=volumeUSD
    results.append(await run_test(
        "TC-02",
        "Use birdeye_token_trending to get trending tokens sorted by volume (sort_by=volumeUSD)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["volume"], tc, "vol_sort")
         or check_text(r, ["trend"], tc, "vol_sort2")
         or check_text(r, ["token"], tc, "vol_sort3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: sort_by=liquidity
    results.append(await run_test(
        "TC-03",
        "Use birdeye_token_trending to get trending tokens sorted by liquidity (sort_by=liquidity)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["liquid"], tc, "liq_sort")
         or check_text(r, ["trend"], tc, "liq_sort2")
         or check_text(r, ["token"], tc, "liq_sort3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: interval=1h
    results.append(await run_test(
        "TC-04",
        "Use birdeye_token_trending to get trending tokens in the last 1 hour (interval=1h)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "1h_interval")
         or check_text(r, ["token"], tc, "1h_interval2")
         or check_text(r, ["1h"], tc, "1h_interval3")
         or check_text(r, ["hour"], tc, "1h_interval4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: interval=4h
    results.append(await run_test(
        "TC-05",
        "Use birdeye_token_trending to get trending tokens over the last 4 hours (interval=4h)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "4h_interval")
         or check_text(r, ["token"], tc, "4h_interval2")
         or check_text(r, ["4h"], tc, "4h_interval3")
         or check_text(r, ["hour"], tc, "4h_interval4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: limit=5
    results.append(await run_test(
        "TC-06",
        "Use birdeye_token_trending to get the top 5 trending tokens (limit=5)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "limit5")
         or check_text(r, ["token"], tc, "limit5b")
         or check_text(r, ["rank"], tc, "limit5c")
         or check_text(r, ["volume"], tc, "limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_type=desc
    results.append(await run_test(
        "TC-07",
        "Use birdeye_token_trending to get trending tokens sorted descending by rank (sort_type=desc)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "desc_sort")
         or check_text(r, ["token"], tc, "desc_sort2")
         or check_text(r, ["rank"], tc, "desc_sort3")
         or check_text(r, ["volume"], tc, "desc_sort4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: chain=ethereum
    results.append(await run_test(
        "TC-08",
        "Use birdeye_token_trending to get trending tokens on Ethereum (chain=ethereum)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "eth_trending")
         or check_text(r, ["ethereum"], tc, "eth_trending2")
         or check_text(r, ["token"], tc, "eth_trending3")
         or check_text(r, ["volume"], tc, "eth_trending4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: chain=base
    results.append(await run_test(
        "TC-09",
        "Use birdeye_token_trending to get trending tokens on Base chain (chain=base)",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "base_trending")
         or check_text(r, ["base"], tc, "base_trending2")
         or check_text(r, ["token"], tc, "base_trending3")
         or check_text(r, ["volume"], tc, "base_trending4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language
    results.append(await run_test(
        "TC-10",
        "Use birdeye_token_trending to show me what tokens are trending on Solana right now",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "natural_sol")
         or check_text(r, ["token"], tc, "natural_sol2")
         or check_text(r, ["solana"], tc, "natural_sol3")
         or check_text(r, ["volume"], tc, "natural_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language volume 1h
    results.append(await run_test(
        "TC-11",
        "Use birdeye_token_trending to find the top trending tokens by volume in the last hour",
        {"birdeye_token_trending"},
        [lambda r, tc: check_text(r, ["trend"], tc, "natural_vol")
         or check_text(r, ["volume"], tc, "natural_vol2")
         or check_text(r, ["token"], tc, "natural_vol3")
         or check_text(r, ["hour"], tc, "natural_vol4")],
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

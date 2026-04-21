"""
Test suite for GET /defi/v2/tokens/new_listing via DeFi Query Service LLM.

Coverage:
  TC-01  Basic new listings (default limit=20)
  TC-02  limit=10
  TC-03  limit=5
  TC-04  meme_platform_enabled=true (include pump.fun)
  TC-05  meme_platform_enabled=false (exclude launchpads)
  TC-06  Natural language "what tokens just launched on Solana"
  TC-07  Natural language "newest Solana token listings"
  TC-08  Natural language "latest pump.fun launches"
  TC-09  Tool selection: new listings → birdeye_new_listings
  TC-10  Natural language "fresh token listings today"
  TC-11  limit=20 + meme_platform_enabled=true
  TC-12  Natural language "what's newly listed on Solana DEX"
  TC-13  Tool selection: "recently launched tokens" → birdeye_new_listings
  TC-14  Natural language "show me the latest token launches including memes"
  TC-15  Natural language "what are the newest coins on Solana"
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
    print("birdeye_new_listings (GET /defi/v2/tokens/new_listing) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic new listings
    results.append(await run_test(
        "TC-01",
        "Use birdeye_new_listings to get the latest token listings on Solana",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "basic_listings")
         or check_text(r, ["listing"], tc, "basic_listings2")
         or check_text(r, ["new"], tc, "basic_listings3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: limit=10
    results.append(await run_test(
        "TC-02",
        "Use birdeye_new_listings to get the 10 most recently listed tokens on Solana (limit=10)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit_10_listings")
         or check_text(r, ["listing"], tc, "limit_10_listings2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: limit=5
    results.append(await run_test(
        "TC-03",
        "Use birdeye_new_listings to get the 5 newest token launches on Solana (limit=5)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit_5_listings")
         or check_text(r, ["listing"], tc, "limit_5_listings2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: meme_platform_enabled=true
    results.append(await run_test(
        "TC-04",
        "Use birdeye_new_listings to get new token launches including pump.fun and meme launchpads (meme_platform_enabled=true)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "meme_enabled")
         or check_text(r, ["pump"], tc, "meme_enabled2")
         or check_text(r, ["listing"], tc, "meme_enabled3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: meme_platform_enabled=false
    results.append(await run_test(
        "TC-05",
        "Use birdeye_new_listings to get new listings excluding meme launchpads (meme_platform_enabled=false)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "meme_disabled")
         or check_text(r, ["listing"], tc, "meme_disabled2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Natural language "just launched"
    results.append(await run_test(
        "TC-06",
        "What tokens just launched on Solana today?",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_just_launched")
         or check_text(r, ["listing"], tc, "natural_just_launched2")
         or check_text(r, ["new"], tc, "natural_just_launched3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Natural language "newest listings"
    results.append(await run_test(
        "TC-07",
        "Show me the newest token listings on Solana",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_newest")
         or check_text(r, ["listing"], tc, "natural_newest2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Natural language "pump.fun launches"
    results.append(await run_test(
        "TC-08",
        "Use birdeye_new_listings to show me the latest launches including pump.fun tokens (meme_platform_enabled=true, limit=10)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_pump_launches")
         or check_text(r, ["pump"], tc, "natural_pump_launches2")
         or check_text(r, ["listing"], tc, "natural_pump_launches3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Tool selection
    results.append(await run_test(
        "TC-09",
        "Use birdeye_new_listings to fetch the most recently listed tokens on Solana DEXes",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "tool_selection_listings")
         or check_text(r, ["listing"], tc, "tool_selection_listings2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "fresh listings"
    results.append(await run_test(
        "TC-10",
        "Use birdeye_new_listings to find fresh token listings on Solana from today",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_fresh")
         or check_text(r, ["listing"], tc, "natural_fresh2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: limit=20 + meme enabled
    results.append(await run_test(
        "TC-11",
        "Use birdeye_new_listings to get 20 new token listings including meme launchpads (limit=20, meme_platform_enabled=true)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit_20_meme")
         or check_text(r, ["listing"], tc, "limit_20_meme2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "newly listed on DEX"
    results.append(await run_test(
        "TC-12",
        "What's newly listed on Solana DEXes? Use new listings data.",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_dex_listed")
         or check_text(r, ["listing"], tc, "natural_dex_listed2")
         or check_text(r, ["new"], tc, "natural_dex_listed3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection "recently launched"
    results.append(await run_test(
        "TC-13",
        "Use birdeye_new_listings to show me tokens that were recently launched on Solana (limit=10)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "recently_launched")
         or check_text(r, ["listing"], tc, "recently_launched2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "latest launches including memes"
    results.append(await run_test(
        "TC-14",
        "Use birdeye_new_listings to show me the latest token launches including meme coins (meme_platform_enabled=true, limit=15)",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "latest_meme_launches")
         or check_text(r, ["listing"], tc, "latest_meme_launches2")
         or check_text(r, ["meme"], tc, "latest_meme_launches3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language "newest coins"
    results.append(await run_test(
        "TC-15",
        "Use birdeye_new_listings to find the newest tokens and coins just listed on Solana",
        {"birdeye_new_listings"},
        [lambda r, tc: check_text(r, ["token"], tc, "newest_coins")
         or check_text(r, ["listing"], tc, "newest_coins2")
         or check_text(r, ["new"], tc, "newest_coins3")],
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

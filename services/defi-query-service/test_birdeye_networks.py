"""
Tests for GET /defi/networks via DeFi Query Service LLM.

TC-01  Basic network list
TC-02  Natural language "what chains does Birdeye support"
TC-03  Natural language "list supported networks on Birdeye"
TC-04  Natural language "which blockchains are available"
TC-05  Natural language "does Birdeye support Ethereum"
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
    print("birdeye_networks (GET /defi/networks) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Basic
    results.append(await run_test(
        "TC-01",
        "Use birdeye_networks to get the list of all supported blockchain networks",
        {"birdeye_networks"},
        [lambda r, tc: check_text(r, ["network"], tc, "basic")
         or check_text(r, ["chain"], tc, "basic2")
         or check_text(r, ["solana"], tc, "basic3")
         or check_text(r, ["blockchain"], tc, "basic4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Natural language chains
    results.append(await run_test(
        "TC-02",
        "Use birdeye_networks to find out what chains Birdeye supports",
        {"birdeye_networks"},
        [lambda r, tc: check_text(r, ["chain"], tc, "natural_chains")
         or check_text(r, ["network"], tc, "natural_chains2")
         or check_text(r, ["solana"], tc, "natural_chains3")
         or check_text(r, ["support"], tc, "natural_chains4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Supported networks
    results.append(await run_test(
        "TC-03",
        "Use birdeye_networks to list all supported networks on Birdeye",
        {"birdeye_networks"},
        [lambda r, tc: check_text(r, ["network"], tc, "list_networks")
         or check_text(r, ["chain"], tc, "list_networks2")
         or check_text(r, ["solana"], tc, "list_networks3")
         or check_text(r, ["blockchain"], tc, "list_networks4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Which blockchains
    results.append(await run_test(
        "TC-04",
        "Use birdeye_networks to show which blockchains are available on Birdeye",
        {"birdeye_networks"},
        [lambda r, tc: check_text(r, ["network"], tc, "which_chains")
         or check_text(r, ["chain"], tc, "which_chains2")
         or check_text(r, ["solana"], tc, "which_chains3")
         or check_text(r, ["blockchain"], tc, "which_chains4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Ethereum support
    results.append(await run_test(
        "TC-05",
        "Use birdeye_networks to check if Ethereum is supported by Birdeye",
        {"birdeye_networks"},
        [lambda r, tc: check_text(r, ["ethereum"], tc, "eth_support")
         or check_text(r, ["chain"], tc, "eth_support2")
         or check_text(r, ["network"], tc, "eth_support3")
         or check_text(r, ["support"], tc, "eth_support4")],
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

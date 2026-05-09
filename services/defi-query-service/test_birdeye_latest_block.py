"""
Tests for GET /defi/v3/txs/latest-block via DeFi Query Service LLM.

TC-01  Solana latest block (default)
TC-02  Ethereum latest block
TC-03  Base latest block
TC-04  Arbitrum latest block
TC-05  BSC latest block
TC-06  Natural language "what is the current Solana block"
TC-07  Natural language "latest block on Ethereum"
TC-08  Natural language "current block number on Base"
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
    print("birdeye_latest_block (GET /defi/v3/txs/latest-block) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Solana default
    results.append(await run_test(
        "TC-01",
        "Use birdeye_latest_block to get the latest block number on Solana",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "sol_block")
         or check_text(r, ["solana"], tc, "sol_block2")
         or check_text(r, ["number"], tc, "sol_block3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Ethereum
    results.append(await run_test(
        "TC-02",
        "Use birdeye_latest_block to get the latest block number on Ethereum (chain=ethereum)",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "eth_block")
         or check_text(r, ["ethereum"], tc, "eth_block2")
         or check_text(r, ["number"], tc, "eth_block3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Base
    results.append(await run_test(
        "TC-03",
        "Use birdeye_latest_block to get the latest block on Base chain (chain=base)",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "base_block")
         or check_text(r, ["base"], tc, "base_block2")
         or check_text(r, ["number"], tc, "base_block3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Arbitrum
    results.append(await run_test(
        "TC-04",
        "Use birdeye_latest_block to get the current block number on Arbitrum (chain=arbitrum)",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "arb_block")
         or check_text(r, ["arbitrum"], tc, "arb_block2")
         or check_text(r, ["number"], tc, "arb_block3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: BSC
    results.append(await run_test(
        "TC-05",
        "Use birdeye_latest_block to get the latest block number on BSC (chain=bsc)",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "bsc_block")
         or check_text(r, ["bsc"], tc, "bsc_block2")
         or check_text(r, ["number"], tc, "bsc_block3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Natural language Solana
    results.append(await run_test(
        "TC-06",
        "Use birdeye_latest_block to find out what the current Solana block number is",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "natural_sol")
         or check_text(r, ["solana"], tc, "natural_sol2")
         or check_text(r, ["number"], tc, "natural_sol3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Natural language Ethereum
    results.append(await run_test(
        "TC-07",
        "Use birdeye_latest_block to check the latest block on Ethereum",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "natural_eth")
         or check_text(r, ["ethereum"], tc, "natural_eth2")
         or check_text(r, ["number"], tc, "natural_eth3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Natural language Base
    results.append(await run_test(
        "TC-08",
        "Use birdeye_latest_block to get the current block number on Base",
        {"birdeye_latest_block"},
        [lambda r, tc: check_text(r, ["block"], tc, "natural_base")
         or check_text(r, ["base"], tc, "natural_base2")
         or check_text(r, ["number"], tc, "natural_base3")],
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

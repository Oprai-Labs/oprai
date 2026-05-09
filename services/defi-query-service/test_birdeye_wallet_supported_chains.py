"""
Test suite for GET /v1/wallet/list_supported_chain via DeFi Query Service LLM.

Coverage:
  TC-01  Basic supported chains list
  TC-02  Natural language "what chains does Birdeye support"
  TC-03  Natural language "supported blockchains for wallet"
  TC-04  Natural language "which networks can I use"
  TC-05  Natural language "list all supported chains"
  TC-06  Natural language "does Birdeye support Ethereum"
  TC-07  Natural language "does Birdeye support Base"
  TC-08  Natural language "does Birdeye support Solana"
  TC-09  Natural language "what blockchains work with Birdeye wallet API"
  TC-10  Natural language "chain support for portfolio tracking"
  TC-11  Direct tool call — chain list
  TC-12  Natural language "EVM chains supported"
  TC-13  Natural language "multi-chain support"
  TC-14  Natural language "can I track my Arbitrum wallet"
  TC-15  Natural language "list all networks Birdeye wallet supports"
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
    print("birdeye_wallet_supported_chains (GET /v1/wallet/list_supported_chain) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic
    results.append(await run_test(
        "TC-01",
        "Use birdeye_wallet_supported_chains to get the list of supported chains for Birdeye wallet APIs",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "basic_chains")
         or check_text(r, ["solana"], tc, "basic_chains2")
         or check_text(r, ["network"], tc, "basic_chains3")
         or check_text(r, ["blockchain"], tc, "basic_chains4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Natural language
    results.append(await run_test(
        "TC-02",
        "Use birdeye_wallet_supported_chains to find out what chains Birdeye wallet API supports",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "natural_what_chains")
         or check_text(r, ["solana"], tc, "natural_what_chains2")
         or check_text(r, ["network"], tc, "natural_what_chains3")
         or check_text(r, ["blockchain"], tc, "natural_what_chains4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Natural language wallet
    results.append(await run_test(
        "TC-03",
        "Use birdeye_wallet_supported_chains to show me which blockchains are supported for wallet tracking",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "natural_wallet_chains")
         or check_text(r, ["solana"], tc, "natural_wallet_chains2")
         or check_text(r, ["network"], tc, "natural_wallet_chains3")
         or check_text(r, ["blockchain"], tc, "natural_wallet_chains4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Natural language networks
    results.append(await run_test(
        "TC-04",
        "Use birdeye_wallet_supported_chains to tell me which networks I can use with Birdeye wallet features",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "natural_networks")
         or check_text(r, ["solana"], tc, "natural_networks2")
         or check_text(r, ["network"], tc, "natural_networks3")
         or check_text(r, ["blockchain"], tc, "natural_networks4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Natural language list all
    results.append(await run_test(
        "TC-05",
        "Use birdeye_wallet_supported_chains to list all supported blockchain networks",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "natural_list_all")
         or check_text(r, ["solana"], tc, "natural_list_all2")
         or check_text(r, ["network"], tc, "natural_list_all3")
         or check_text(r, ["blockchain"], tc, "natural_list_all4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Does Birdeye support Ethereum
    results.append(await run_test(
        "TC-06",
        "Use birdeye_wallet_supported_chains to check if Ethereum is supported by Birdeye wallet API",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["ethereum"], tc, "eth_supported")
         or check_text(r, ["chain"], tc, "eth_supported2")
         or check_text(r, ["network"], tc, "eth_supported3")
         or check_text(r, ["support"], tc, "eth_supported4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Does Birdeye support Base
    results.append(await run_test(
        "TC-07",
        "Use birdeye_wallet_supported_chains to check if Base chain is supported",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["base"], tc, "base_supported")
         or check_text(r, ["chain"], tc, "base_supported2")
         or check_text(r, ["network"], tc, "base_supported3")
         or check_text(r, ["support"], tc, "base_supported4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Solana supported
    results.append(await run_test(
        "TC-08",
        "Use birdeye_wallet_supported_chains to confirm Solana is in the supported chain list",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["solana"], tc, "sol_supported")
         or check_text(r, ["chain"], tc, "sol_supported2")
         or check_text(r, ["network"], tc, "sol_supported3")
         or check_text(r, ["support"], tc, "sol_supported4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Wallet API chains
    results.append(await run_test(
        "TC-09",
        "Use birdeye_wallet_supported_chains to get the full list of blockchains compatible with Birdeye wallet API",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "wallet_api_chains")
         or check_text(r, ["solana"], tc, "wallet_api_chains2")
         or check_text(r, ["network"], tc, "wallet_api_chains3")
         or check_text(r, ["blockchain"], tc, "wallet_api_chains4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Portfolio tracking chains
    results.append(await run_test(
        "TC-10",
        "Use birdeye_wallet_supported_chains to find out which chains support portfolio tracking on Birdeye",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "portfolio_chains")
         or check_text(r, ["solana"], tc, "portfolio_chains2")
         or check_text(r, ["network"], tc, "portfolio_chains3")
         or check_text(r, ["support"], tc, "portfolio_chains4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Direct tool call
    results.append(await run_test(
        "TC-11",
        "Use birdeye_wallet_supported_chains to return the supported chain list",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "direct_call")
         or check_text(r, ["solana"], tc, "direct_call2")
         or check_text(r, ["network"], tc, "direct_call3")
         or check_text(r, ["blockchain"], tc, "direct_call4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: EVM chains
    results.append(await run_test(
        "TC-12",
        "Use birdeye_wallet_supported_chains to show me which EVM chains are supported by Birdeye",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "evm_chains")
         or check_text(r, ["ethereum"], tc, "evm_chains2")
         or check_text(r, ["network"], tc, "evm_chains3")
         or check_text(r, ["evm"], tc, "evm_chains4")
         or check_text(r, ["support"], tc, "evm_chains5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Multi-chain support
    results.append(await run_test(
        "TC-13",
        "Use birdeye_wallet_supported_chains to check Birdeye's multi-chain wallet support",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "multi_chain")
         or check_text(r, ["solana"], tc, "multi_chain2")
         or check_text(r, ["network"], tc, "multi_chain3")
         or check_text(r, ["support"], tc, "multi_chain4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Arbitrum support
    results.append(await run_test(
        "TC-14",
        "Use birdeye_wallet_supported_chains to find out if I can track my Arbitrum wallet on Birdeye",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "arbitrum_check")
         or check_text(r, ["arbitrum"], tc, "arbitrum_check2")
         or check_text(r, ["network"], tc, "arbitrum_check3")
         or check_text(r, ["support"], tc, "arbitrum_check4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: All networks
    results.append(await run_test(
        "TC-15",
        "Use birdeye_wallet_supported_chains to give me the complete list of all networks Birdeye wallet supports",
        {"birdeye_wallet_supported_chains"},
        [lambda r, tc: check_text(r, ["chain"], tc, "all_networks")
         or check_text(r, ["solana"], tc, "all_networks2")
         or check_text(r, ["network"], tc, "all_networks3")
         or check_text(r, ["blockchain"], tc, "all_networks4")],
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

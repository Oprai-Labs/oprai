"""
Test suite for GET /wallet/v2/net-worth-details via DeFi Query Service LLM.

Coverage:
  TC-01  Default asset breakdown
  TC-02  type=1h (hourly snapshot)
  TC-03  type=1d (daily snapshot)
  TC-04  sort_type=asc (smallest first)
  TC-05  limit=5 (top 5 assets)
  TC-06  limit=50
  TC-07  offset=20 (pagination)
  TC-08  chain=solana (explicit)
  TC-09  chain=ethereum
  TC-10  chain=base
  TC-11  Different wallet address
  TC-12  limit=5 + sort_type=asc
  TC-13  Natural language "what was in wallet X at this time"
  TC-14  Natural language "asset breakdown for wallet"
  TC-15  type=1h + limit=10 + offset=10
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

WALLET_1 = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
WALLET_2 = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"

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
    print("birdeye_wallet_net_worth_details (GET /wallet/v2/net-worth-details) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Default breakdown
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_net_worth_details to get the asset breakdown for wallet {WALLET_1}",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "default_breakdown")
         or check_text(r, ["asset"], tc, "default_breakdown2")
         or check_text(r, ["usd"], tc, "default_breakdown3")
         or check_text(r, ["portfolio"], tc, "default_breakdown4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: type=1h
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_net_worth_details to get hourly asset breakdown for wallet {WALLET_1} (type=1h)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "type_1h")
         or check_text(r, ["asset"], tc, "type_1h2")
         or check_text(r, ["usd"], tc, "type_1h3")
         or check_text(r, ["portfolio"], tc, "type_1h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: type=1d
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_net_worth_details to get daily asset breakdown for wallet {WALLET_1} (type=1d, limit=10)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "type_1d")
         or check_text(r, ["asset"], tc, "type_1d2")
         or check_text(r, ["usd"], tc, "type_1d3")
         or check_text(r, ["portfolio"], tc, "type_1d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: sort_type=asc
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_net_worth_details to get wallet {WALLET_1} assets sorted by value ascending (sort_type=asc, limit=10)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "sort_asc")
         or check_text(r, ["asset"], tc, "sort_asc2")
         or check_text(r, ["usd"], tc, "sort_asc3")
         or check_text(r, ["portfolio"], tc, "sort_asc4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: limit=5
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_net_worth_details to get the top 5 assets in wallet {WALLET_1} (limit=5)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit5")
         or check_text(r, ["asset"], tc, "limit5b")
         or check_text(r, ["usd"], tc, "limit5c")
         or check_text(r, ["portfolio"], tc, "limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: limit=50
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_net_worth_details to get up to 50 assets for wallet {WALLET_1} (limit=50)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit50")
         or check_text(r, ["asset"], tc, "limit50b")
         or check_text(r, ["usd"], tc, "limit50c")
         or check_text(r, ["portfolio"], tc, "limit50d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: offset pagination
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_net_worth_details to get the next page of assets for wallet {WALLET_1} (offset=20, limit=10)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "offset_page")
         or check_text(r, ["asset"], tc, "offset_page2")
         or check_text(r, ["usd"], tc, "offset_page3")
         or check_text(r, ["portfolio"], tc, "offset_page4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: chain=solana
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_net_worth_details to get the Solana asset breakdown for wallet {WALLET_1} (chain=solana, limit=10)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["sol"], tc, "chain_sol")
         or check_text(r, ["token"], tc, "chain_sol2")
         or check_text(r, ["asset"], tc, "chain_sol3")
         or check_text(r, ["usd"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: chain=ethereum
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_net_worth_details to get Ethereum asset details for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=ethereum, limit=10)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_eth")
         or check_text(r, ["asset"], tc, "chain_eth2")
         or check_text(r, ["usd"], tc, "chain_eth3")
         or check_text(r, ["ethereum"], tc, "chain_eth4")
         or check_text(r, ["portfolio"], tc, "chain_eth5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: chain=base
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_net_worth_details to get Base chain asset breakdown for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=base, limit=10)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_base")
         or check_text(r, ["asset"], tc, "chain_base2")
         or check_text(r, ["usd"], tc, "chain_base3")
         or check_text(r, ["base"], tc, "chain_base4")
         or check_text(r, ["portfolio"], tc, "chain_base5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Different wallet
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_net_worth_details to get the net worth asset details for wallet {WALLET_2}, limit=10",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "wallet2_details")
         or check_text(r, ["asset"], tc, "wallet2_details2")
         or check_text(r, ["usd"], tc, "wallet2_details3")
         or check_text(r, ["portfolio"], tc, "wallet2_details4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: limit=5 + sort_type=asc
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_net_worth_details to get the 5 smallest holdings for wallet {WALLET_1} (limit=5, sort_type=asc)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "smallest5")
         or check_text(r, ["asset"], tc, "smallest5b")
         or check_text(r, ["usd"], tc, "smallest5c")
         or check_text(r, ["portfolio"], tc, "smallest5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "what was in wallet at this time"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_net_worth_details to show me the detailed asset composition of wallet {WALLET_1}",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_composition")
         or check_text(r, ["asset"], tc, "natural_composition2")
         or check_text(r, ["usd"], tc, "natural_composition3")
         or check_text(r, ["portfolio"], tc, "natural_composition4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "asset breakdown"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_net_worth_details to give me the full asset breakdown and value details for wallet {WALLET_1}",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_breakdown")
         or check_text(r, ["asset"], tc, "natural_breakdown2")
         or check_text(r, ["usd"], tc, "natural_breakdown3")
         or check_text(r, ["portfolio"], tc, "natural_breakdown4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: type=1h + limit=10 + offset=10
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_net_worth_details to get the second page of hourly asset details for wallet {WALLET_1} (type=1h, limit=10, offset=10)",
        {"birdeye_wallet_net_worth_details"},
        [lambda r, tc: check_text(r, ["token"], tc, "hourly_page2")
         or check_text(r, ["asset"], tc, "hourly_page2b")
         or check_text(r, ["usd"], tc, "hourly_page2c")
         or check_text(r, ["portfolio"], tc, "hourly_page2d")],
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

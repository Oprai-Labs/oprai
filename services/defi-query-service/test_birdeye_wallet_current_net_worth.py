"""
Test suite for GET /wallet/v2/current-net-worth via DeFi Query Service LLM.

Coverage:
  TC-01  Basic portfolio snapshot (default)
  TC-02  filter_value=$1 (hide dust)
  TC-03  filter_value=$100 (significant holdings only)
  TC-04  sort_type=asc (smallest first)
  TC-05  limit=5
  TC-06  limit=50
  TC-07  offset=20 (pagination)
  TC-08  flags=include_low_liquidity
  TC-09  chain=solana (explicit)
  TC-10  chain=ethereum
  TC-11  chain=base
  TC-12  Different wallet address
  TC-13  Natural language "what tokens does this wallet hold"
  TC-14  Natural language "portfolio of address"
  TC-15  filter_value + sort_type combined
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
    print("birdeye_wallet_current_net_worth (GET /wallet/v2/current-net-worth) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic portfolio
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_current_net_worth to get the current portfolio for wallet {WALLET_1}",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "portfolio_basic")
         or check_text(r, ["balance"], tc, "portfolio_basic2")
         or check_text(r, ["portfolio"], tc, "portfolio_basic3")
         or check_text(r, ["usd"], tc, "portfolio_basic4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: filter_value=$1
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_current_net_worth to get wallet {WALLET_1} holdings worth at least $1 (filter_value=1)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "filter_1")
         or check_text(r, ["balance"], tc, "filter_1b")
         or check_text(r, ["portfolio"], tc, "filter_1c")
         or check_text(r, ["usd"], tc, "filter_1d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: filter_value=$100
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_current_net_worth to get significant holdings for wallet {WALLET_1} worth over $100 (filter_value=100)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "filter_100")
         or check_text(r, ["balance"], tc, "filter_100b")
         or check_text(r, ["portfolio"], tc, "filter_100c")
         or check_text(r, ["usd"], tc, "filter_100d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: sort_type=asc
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_current_net_worth to get wallet {WALLET_1} holdings sorted by value ascending (sort_type=asc, limit=10)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "sort_asc")
         or check_text(r, ["balance"], tc, "sort_asc2")
         or check_text(r, ["portfolio"], tc, "sort_asc3")
         or check_text(r, ["usd"], tc, "sort_asc4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: limit=5
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_current_net_worth to get the top 5 holdings for wallet {WALLET_1} (limit=5)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit5")
         or check_text(r, ["balance"], tc, "limit5b")
         or check_text(r, ["portfolio"], tc, "limit5c")
         or check_text(r, ["usd"], tc, "limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: limit=50
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_current_net_worth to get up to 50 token holdings for wallet {WALLET_1} (limit=50)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "limit50")
         or check_text(r, ["balance"], tc, "limit50b")
         or check_text(r, ["portfolio"], tc, "limit50c")
         or check_text(r, ["usd"], tc, "limit50d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: offset pagination
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_current_net_worth to get the next page of holdings for wallet {WALLET_1} (offset=20, limit=10)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "offset_page")
         or check_text(r, ["balance"], tc, "offset_page2")
         or check_text(r, ["portfolio"], tc, "offset_page3")
         or check_text(r, ["usd"], tc, "offset_page4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: flags=include_low_liquidity
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_current_net_worth to get all holdings including low liquidity tokens for wallet {WALLET_1} (flags=[include_low_liquidity], limit=20)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "low_liq")
         or check_text(r, ["balance"], tc, "low_liq2")
         or check_text(r, ["portfolio"], tc, "low_liq3")
         or check_text(r, ["usd"], tc, "low_liq4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: chain=solana explicit
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_current_net_worth to get the Solana portfolio for wallet {WALLET_1} (chain=solana)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["sol"], tc, "chain_solana")
         or check_text(r, ["token"], tc, "chain_solana2")
         or check_text(r, ["portfolio"], tc, "chain_solana3")
         or check_text(r, ["balance"], tc, "chain_solana4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: chain=ethereum
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_current_net_worth to get the Ethereum portfolio for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=ethereum, limit=10)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_eth")
         or check_text(r, ["balance"], tc, "chain_eth2")
         or check_text(r, ["portfolio"], tc, "chain_eth3")
         or check_text(r, ["ethereum"], tc, "chain_eth4")
         or check_text(r, ["eth"], tc, "chain_eth5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: chain=base
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_current_net_worth to get the Base chain portfolio for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=base, limit=10)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_base")
         or check_text(r, ["balance"], tc, "chain_base2")
         or check_text(r, ["portfolio"], tc, "chain_base3")
         or check_text(r, ["base"], tc, "chain_base4")
         or check_text(r, ["usd"], tc, "chain_base5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Different wallet
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_current_net_worth to get the current net worth breakdown for wallet {WALLET_2}, limit=10",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "wallet2_nw")
         or check_text(r, ["balance"], tc, "wallet2_nw2")
         or check_text(r, ["portfolio"], tc, "wallet2_nw3")
         or check_text(r, ["usd"], tc, "wallet2_nw4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "what tokens does this wallet hold"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_current_net_worth to show me what tokens wallet {WALLET_1} is holding right now",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_holdings")
         or check_text(r, ["balance"], tc, "natural_holdings2")
         or check_text(r, ["portfolio"], tc, "natural_holdings3")
         or check_text(r, ["usd"], tc, "natural_holdings4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "portfolio of address"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_current_net_worth to give me the full portfolio breakdown for address {WALLET_1}",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_portfolio")
         or check_text(r, ["balance"], tc, "natural_portfolio2")
         or check_text(r, ["portfolio"], tc, "natural_portfolio3")
         or check_text(r, ["usd"], tc, "natural_portfolio4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: filter_value + sort_type combined
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_current_net_worth to get wallet {WALLET_1} holdings worth over $10 sorted from smallest to largest (filter_value=10, sort_type=asc, limit=20)",
        {"birdeye_wallet_current_net_worth"},
        [lambda r, tc: check_text(r, ["token"], tc, "filter_sort_combo")
         or check_text(r, ["balance"], tc, "filter_sort_combo2")
         or check_text(r, ["portfolio"], tc, "filter_sort_combo3")
         or check_text(r, ["usd"], tc, "filter_sort_combo4")],
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

"""
Test suite for GET /wallet/v2/net-worth via DeFi Query Service LLM.

Coverage:
  TC-01  Default (7 daily data points)
  TC-02  type=1h (hourly)
  TC-03  count=14 (two weeks)
  TC-04  count=30 (one month)
  TC-05  direction=back
  TC-06  direction=forward
  TC-07  sort_type=asc (oldest first)
  TC-08  chain=solana (explicit)
  TC-09  chain=ethereum
  TC-10  chain=base
  TC-11  count=1 (single snapshot)
  TC-12  Different wallet address
  TC-13  Natural language "how has wallet X grown"
  TC-14  Natural language "net worth history last 7 days"
  TC-15  type=1h + count=24 (last 24 hours hourly)
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
    print("birdeye_wallet_net_worth_history (GET /wallet/v2/net-worth) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Default 7 daily points
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_net_worth_history to get the net worth history for wallet {WALLET_1}",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "history_default")
         or check_text(r, ["usd"], tc, "history_default2")
         or check_text(r, ["portfolio"], tc, "history_default3")
         or check_text(r, ["value"], tc, "history_default4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: type=1h (hourly)
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_net_worth_history to get hourly net worth data for wallet {WALLET_1} (type=1h, count=24)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "hourly")
         or check_text(r, ["usd"], tc, "hourly2")
         or check_text(r, ["portfolio"], tc, "hourly3")
         or check_text(r, ["value"], tc, "hourly4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: count=14
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_net_worth_history to get 14 days of net worth history for wallet {WALLET_1} (count=14, type=1d)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "14days")
         or check_text(r, ["usd"], tc, "14days2")
         or check_text(r, ["portfolio"], tc, "14days3")
         or check_text(r, ["value"], tc, "14days4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: count=30
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_net_worth_history to get 30 days of net worth history for wallet {WALLET_1} (count=30, type=1d)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "30days")
         or check_text(r, ["usd"], tc, "30days2")
         or check_text(r, ["portfolio"], tc, "30days3")
         or check_text(r, ["value"], tc, "30days4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: direction=back
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_net_worth_history to get historical net worth going backwards for wallet {WALLET_1} (direction=back, count=7)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "dir_back")
         or check_text(r, ["usd"], tc, "dir_back2")
         or check_text(r, ["portfolio"], tc, "dir_back3")
         or check_text(r, ["value"], tc, "dir_back4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: direction=forward
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_net_worth_history to get net worth data going forward for wallet {WALLET_1} (direction=forward, count=7)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "dir_forward")
         or check_text(r, ["usd"], tc, "dir_forward2")
         or check_text(r, ["portfolio"], tc, "dir_forward3")
         or check_text(r, ["value"], tc, "dir_forward4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_type=asc
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_net_worth_history to get net worth history for wallet {WALLET_1} sorted oldest first (sort_type=asc, count=7)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "sort_asc")
         or check_text(r, ["usd"], tc, "sort_asc2")
         or check_text(r, ["portfolio"], tc, "sort_asc3")
         or check_text(r, ["value"], tc, "sort_asc4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: chain=solana explicit
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_net_worth_history to get the Solana net worth history for wallet {WALLET_1} (chain=solana, count=7)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "chain_sol")
         or check_text(r, ["usd"], tc, "chain_sol2")
         or check_text(r, ["portfolio"], tc, "chain_sol3")
         or check_text(r, ["value"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: chain=ethereum
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_net_worth_history to get Ethereum net worth history for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=ethereum, count=7)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "chain_eth")
         or check_text(r, ["usd"], tc, "chain_eth2")
         or check_text(r, ["portfolio"], tc, "chain_eth3")
         or check_text(r, ["value"], tc, "chain_eth4")
         or check_text(r, ["ethereum"], tc, "chain_eth5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: chain=base
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_net_worth_history to get Base chain net worth history for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=base, count=7)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "chain_base")
         or check_text(r, ["usd"], tc, "chain_base2")
         or check_text(r, ["portfolio"], tc, "chain_base3")
         or check_text(r, ["value"], tc, "chain_base4")
         or check_text(r, ["base"], tc, "chain_base5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: count=1
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_net_worth_history to get a single net worth data point for wallet {WALLET_1} (count=1)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "count1")
         or check_text(r, ["usd"], tc, "count1b")
         or check_text(r, ["portfolio"], tc, "count1c")
         or check_text(r, ["value"], tc, "count1d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Different wallet
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_net_worth_history to get the 7-day net worth history for wallet {WALLET_2}",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "wallet2_hist")
         or check_text(r, ["usd"], tc, "wallet2_hist2")
         or check_text(r, ["portfolio"], tc, "wallet2_hist3")
         or check_text(r, ["value"], tc, "wallet2_hist4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "how has wallet grown"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_net_worth_history to show me how wallet {WALLET_1}'s total value has changed over the past week",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "natural_growth")
         or check_text(r, ["usd"], tc, "natural_growth2")
         or check_text(r, ["portfolio"], tc, "natural_growth3")
         or check_text(r, ["value"], tc, "natural_growth4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "net worth last 7 days"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_net_worth_history to give me the net worth chart for wallet {WALLET_1} for the last 7 days",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "natural_7d")
         or check_text(r, ["usd"], tc, "natural_7d2")
         or check_text(r, ["portfolio"], tc, "natural_7d3")
         or check_text(r, ["value"], tc, "natural_7d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: type=1h + count=24
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_net_worth_history to get the last 24 hourly net worth snapshots for wallet {WALLET_1} (type=1h, count=24)",
        {"birdeye_wallet_net_worth_history"},
        [lambda r, tc: check_text(r, ["net worth"], tc, "hourly_24")
         or check_text(r, ["usd"], tc, "hourly_24b")
         or check_text(r, ["portfolio"], tc, "hourly_24c")
         or check_text(r, ["value"], tc, "hourly_24d")],
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

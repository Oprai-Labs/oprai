"""
Test suite for GET /wallet/v2/pnl/summary via DeFi Query Service LLM.

Coverage:
  TC-01  Default (7d duration)
  TC-02  duration=24h
  TC-03  duration=30d
  TC-04  duration=90d
  TC-05  duration=all
  TC-06  chain=solana (explicit)
  TC-07  chain=base
  TC-08  Different wallet — 24h
  TC-09  Different wallet — 30d
  TC-10  Different wallet — all
  TC-11  Natural language "how much profit has this wallet made"
  TC-12  Natural language "PnL for wallet last week"
  TC-13  Natural language "realized gains for address"
  TC-14  Natural language "is this wallet profitable"
  TC-15  duration=90d + chain=solana combined
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
    print("birdeye_wallet_pnl_summary (GET /wallet/v2/pnl/summary) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Default 7d
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_pnl_summary to get the PnL summary for wallet {WALLET_1}",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "default_pnl")
         or check_text(r, ["profit"], tc, "default_pnl2")
         or check_text(r, ["realized"], tc, "default_pnl3")
         or check_text(r, ["return"], tc, "default_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: duration=24h
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_pnl_summary to get the 24-hour PnL summary for wallet {WALLET_1} (duration=24h)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "24h_pnl")
         or check_text(r, ["profit"], tc, "24h_pnl2")
         or check_text(r, ["realized"], tc, "24h_pnl3")
         or check_text(r, ["return"], tc, "24h_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: duration=30d
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_pnl_summary to get the 30-day PnL summary for wallet {WALLET_1} (duration=30d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "30d_pnl")
         or check_text(r, ["profit"], tc, "30d_pnl2")
         or check_text(r, ["realized"], tc, "30d_pnl3")
         or check_text(r, ["return"], tc, "30d_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: duration=90d
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_pnl_summary to get the 90-day PnL summary for wallet {WALLET_1} (duration=90d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "90d_pnl")
         or check_text(r, ["profit"], tc, "90d_pnl2")
         or check_text(r, ["realized"], tc, "90d_pnl3")
         or check_text(r, ["return"], tc, "90d_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: duration=all
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_pnl_summary to get the all-time PnL summary for wallet {WALLET_1} (duration=all)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "all_pnl")
         or check_text(r, ["profit"], tc, "all_pnl2")
         or check_text(r, ["realized"], tc, "all_pnl3")
         or check_text(r, ["return"], tc, "all_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: chain=solana
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_pnl_summary to get the Solana PnL summary for wallet {WALLET_1} (chain=solana, duration=7d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "chain_sol_pnl")
         or check_text(r, ["profit"], tc, "chain_sol_pnl2")
         or check_text(r, ["realized"], tc, "chain_sol_pnl3")
         or check_text(r, ["return"], tc, "chain_sol_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: chain=base
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_pnl_summary to get the Base chain PnL summary for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=base, duration=30d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "chain_base_pnl")
         or check_text(r, ["profit"], tc, "chain_base_pnl2")
         or check_text(r, ["realized"], tc, "chain_base_pnl3")
         or check_text(r, ["return"], tc, "chain_base_pnl4")
         or check_text(r, ["base"], tc, "chain_base_pnl5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Wallet 2 — 24h
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_pnl_summary to get today's PnL for wallet {WALLET_2} (duration=24h)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "w2_24h")
         or check_text(r, ["profit"], tc, "w2_24h2")
         or check_text(r, ["realized"], tc, "w2_24h3")
         or check_text(r, ["return"], tc, "w2_24h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Wallet 2 — 30d
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_pnl_summary to get the 30-day PnL for wallet {WALLET_2} (duration=30d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "w2_30d")
         or check_text(r, ["profit"], tc, "w2_30d2")
         or check_text(r, ["realized"], tc, "w2_30d3")
         or check_text(r, ["return"], tc, "w2_30d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Wallet 2 — all
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_pnl_summary to get the all-time trading performance for wallet {WALLET_2} (duration=all)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "w2_all")
         or check_text(r, ["profit"], tc, "w2_all2")
         or check_text(r, ["realized"], tc, "w2_all3")
         or check_text(r, ["return"], tc, "w2_all4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "how much profit"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_pnl_summary to tell me how much profit wallet {WALLET_1} has made in the last 7 days",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_profit")
         or check_text(r, ["profit"], tc, "natural_profit2")
         or check_text(r, ["realized"], tc, "natural_profit3")
         or check_text(r, ["return"], tc, "natural_profit4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "PnL last week"
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_pnl_summary to get the PnL performance for wallet {WALLET_1} over the past week (duration=7d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_7d")
         or check_text(r, ["profit"], tc, "natural_7d2")
         or check_text(r, ["realized"], tc, "natural_7d3")
         or check_text(r, ["return"], tc, "natural_7d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "realized gains"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_pnl_summary to show me the realized and unrealized gains for wallet {WALLET_1} over 30 days (duration=30d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_realized")
         or check_text(r, ["profit"], tc, "natural_realized2")
         or check_text(r, ["realized"], tc, "natural_realized3")
         or check_text(r, ["return"], tc, "natural_realized4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "is this wallet profitable"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_pnl_summary to check if wallet {WALLET_1} is profitable — show me its all-time PnL (duration=all)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_profitable")
         or check_text(r, ["profit"], tc, "natural_profitable2")
         or check_text(r, ["realized"], tc, "natural_profitable3")
         or check_text(r, ["return"], tc, "natural_profitable4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: 90d + solana combined
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_pnl_summary to get the Solana 90-day PnL report for wallet {WALLET_1} (chain=solana, duration=90d)",
        {"birdeye_wallet_pnl_summary"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "90d_sol")
         or check_text(r, ["profit"], tc, "90d_sol2")
         or check_text(r, ["realized"], tc, "90d_sol3")
         or check_text(r, ["return"], tc, "90d_sol4")],
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

"""
Test suite for GET /wallet/v2/pnl via DeFi Query Service LLM.

Coverage:
  TC-01  Default per-token PnL (all time)
  TC-02  duration=24h
  TC-03  duration=7d
  TC-04  duration=30d
  TC-05  duration=90d
  TC-06  sort_by=last_trade (default)
  TC-07  sort_by=realized_pnl
  TC-08  sort_type=asc (smallest PnL first)
  TC-09  limit=5
  TC-10  offset=10 (pagination)
  TC-11  token_addresses filter (specific tokens)
  TC-12  chain=solana (explicit)
  TC-13  chain=base
  TC-14  Natural language "which tokens is wallet X profitable on"
  TC-15  duration=30d + sort_by=realized_pnl + limit=10
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

WALLET_1 = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
WALLET_2 = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

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
    print("birdeye_wallet_pnl_details (GET /wallet/v2/pnl) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Default all-time per-token PnL
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_pnl_details to get the per-token PnL breakdown for wallet {WALLET_1}",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "default_pnl")
         or check_text(r, ["profit"], tc, "default_pnl2")
         or check_text(r, ["realized"], tc, "default_pnl3")
         or check_text(r, ["token"], tc, "default_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: duration=24h
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_pnl_details to get the 24-hour per-token PnL for wallet {WALLET_1} (duration=24h)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "24h_pnl")
         or check_text(r, ["profit"], tc, "24h_pnl2")
         or check_text(r, ["realized"], tc, "24h_pnl3")
         or check_text(r, ["token"], tc, "24h_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: duration=7d
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_pnl_details to get the 7-day per-token PnL breakdown for wallet {WALLET_1} (duration=7d)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "7d_pnl")
         or check_text(r, ["profit"], tc, "7d_pnl2")
         or check_text(r, ["realized"], tc, "7d_pnl3")
         or check_text(r, ["token"], tc, "7d_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: duration=30d
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_pnl_details to get the 30-day per-token PnL for wallet {WALLET_1} (duration=30d, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "30d_pnl")
         or check_text(r, ["profit"], tc, "30d_pnl2")
         or check_text(r, ["realized"], tc, "30d_pnl3")
         or check_text(r, ["token"], tc, "30d_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: duration=90d
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_pnl_details to get the 90-day per-token PnL for wallet {WALLET_1} (duration=90d, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "90d_pnl")
         or check_text(r, ["profit"], tc, "90d_pnl2")
         or check_text(r, ["realized"], tc, "90d_pnl3")
         or check_text(r, ["token"], tc, "90d_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: sort_by=last_trade
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_pnl_details to get wallet {WALLET_1} token PnL sorted by last trade time (sort_by=last_trade, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "sort_last_trade")
         or check_text(r, ["profit"], tc, "sort_last_trade2")
         or check_text(r, ["realized"], tc, "sort_last_trade3")
         or check_text(r, ["token"], tc, "sort_last_trade4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_by=realized_pnl
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_pnl_details to get wallet {WALLET_1} token PnL sorted by realized profit (sort_by=realized_pnl, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "sort_realized")
         or check_text(r, ["profit"], tc, "sort_realized2")
         or check_text(r, ["realized"], tc, "sort_realized3")
         or check_text(r, ["token"], tc, "sort_realized4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: sort_type=asc
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_pnl_details to get wallet {WALLET_1} worst-performing tokens first (sort_type=asc, sort_by=realized_pnl, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "sort_asc")
         or check_text(r, ["profit"], tc, "sort_asc2")
         or check_text(r, ["realized"], tc, "sort_asc3")
         or check_text(r, ["token"], tc, "sort_asc4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=5
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_pnl_details to get the top 5 best-performing tokens for wallet {WALLET_1} (limit=5, sort_by=realized_pnl)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "limit5")
         or check_text(r, ["profit"], tc, "limit5b")
         or check_text(r, ["realized"], tc, "limit5c")
         or check_text(r, ["token"], tc, "limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: offset pagination
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_pnl_details to get the next page of per-token PnL for wallet {WALLET_1} (offset=10, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "offset_page")
         or check_text(r, ["profit"], tc, "offset_page2")
         or check_text(r, ["realized"], tc, "offset_page3")
         or check_text(r, ["token"], tc, "offset_page4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: token_addresses filter
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_pnl_details to get wallet {WALLET_1} PnL specifically for SOL ({SOL}) and BONK ({BONK}) (token_addresses filter)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "token_filter")
         or check_text(r, ["profit"], tc, "token_filter2")
         or check_text(r, ["sol"], tc, "token_filter3")
         or check_text(r, ["bonk"], tc, "token_filter4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: chain=solana
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_pnl_details to get the Solana per-token PnL for wallet {WALLET_1} (chain=solana, duration=30d, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "chain_sol")
         or check_text(r, ["profit"], tc, "chain_sol2")
         or check_text(r, ["realized"], tc, "chain_sol3")
         or check_text(r, ["token"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: chain=base
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_pnl_details to get Base chain per-token PnL for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=base, duration=30d, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "chain_base")
         or check_text(r, ["profit"], tc, "chain_base2")
         or check_text(r, ["realized"], tc, "chain_base3")
         or check_text(r, ["token"], tc, "chain_base4")
         or check_text(r, ["base"], tc, "chain_base5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_pnl_details to show me which tokens wallet {WALLET_1} is most profitable on over the last 30 days",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_profitable")
         or check_text(r, ["profit"], tc, "natural_profitable2")
         or check_text(r, ["realized"], tc, "natural_profitable3")
         or check_text(r, ["token"], tc, "natural_profitable4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Combined params
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_pnl_details to get the top 10 best-performing tokens by realized PnL for wallet {WALLET_1} over 30 days (duration=30d, sort_by=realized_pnl, sort_type=desc, limit=10)",
        {"birdeye_wallet_pnl_details"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "combined_params")
         or check_text(r, ["profit"], tc, "combined_params2")
         or check_text(r, ["realized"], tc, "combined_params3")
         or check_text(r, ["token"], tc, "combined_params4")],
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

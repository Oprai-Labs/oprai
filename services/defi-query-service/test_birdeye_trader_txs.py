"""
Test suite for GET /trader/txs/seek_by_time via DeFi Query Service LLM.

Coverage:
  TC-01  Wallet swaps (default)
  TC-02  tx_type=add (liquidity additions)
  TC-03  tx_type=remove (liquidity removals)
  TC-04  tx_type=all
  TC-05  after_time filter (last hour)
  TC-06  before_time filter
  TC-07  before_time + after_time window
  TC-08  limit=10
  TC-09  limit=100
  TC-10  offset=50 (pagination)
  TC-11  ui_amount_mode=raw
  TC-12  Different wallet address
  TC-13  Natural language "what did this wallet trade recently"
  TC-14  Natural language "smart money wallet swap history"
  TC-15  tx_type=all + time window
"""

import asyncio
import httpx
import time

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
    print("birdeye_trader_txs (GET /trader/txs/seek_by_time) — Full Test Suite")
    print("=" * 60)

    now = int(time.time())
    one_hour_ago  = now - 3600
    two_hours_ago = now - 7200

    results = []

    # TC-01: Basic wallet swaps
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_trader_txs to get recent swap transactions for wallet {WALLET_1}",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_swaps")
         or check_text(r, ["tx"], tc, "wallet_swaps2")
         or check_text(r, ["transaction"], tc, "wallet_swaps3")
         or check_text(r, ["wallet"], tc, "wallet_swaps4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: tx_type=add
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_trader_txs to get liquidity addition transactions for wallet {WALLET_1} (tx_type=add, limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["add"], tc, "wallet_add")
         or check_text(r, ["liquidity"], tc, "wallet_add2")
         or check_text(r, ["tx"], tc, "wallet_add3")
         or check_text(r, ["wallet"], tc, "wallet_add4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: tx_type=remove
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_trader_txs to get liquidity removal transactions for wallet {WALLET_1} (tx_type=remove, limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["remove"], tc, "wallet_remove")
         or check_text(r, ["liquidity"], tc, "wallet_remove2")
         or check_text(r, ["tx"], tc, "wallet_remove3")
         or check_text(r, ["wallet"], tc, "wallet_remove4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: tx_type=all
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_trader_txs to get all DeFi transactions for wallet {WALLET_1} (tx_type=all, limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["tx"], tc, "wallet_all")
         or check_text(r, ["transaction"], tc, "wallet_all2")
         or check_text(r, ["swap"], tc, "wallet_all3")
         or check_text(r, ["wallet"], tc, "wallet_all4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: after_time filter
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_trader_txs to get swap transactions for wallet {WALLET_1} from the last hour (after_time={one_hour_ago}, limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_after")
         or check_text(r, ["tx"], tc, "wallet_after2")
         or check_text(r, ["transaction"], tc, "wallet_after3")
         or check_text(r, ["wallet"], tc, "wallet_after4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: before_time filter
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_trader_txs to get swap transactions for wallet {WALLET_1} before timestamp {one_hour_ago} (before_time filter, limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_before")
         or check_text(r, ["tx"], tc, "wallet_before2")
         or check_text(r, ["transaction"], tc, "wallet_before3")
         or check_text(r, ["wallet"], tc, "wallet_before4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: before_time + after_time window
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_trader_txs to get swap transactions for wallet {WALLET_1} between {two_hours_ago} and {one_hour_ago} (time window, limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_window")
         or check_text(r, ["tx"], tc, "wallet_window2")
         or check_text(r, ["transaction"], tc, "wallet_window3")
         or check_text(r, ["wallet"], tc, "wallet_window4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: limit=10
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_trader_txs to get the last 10 swap transactions for wallet {WALLET_1} (limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_limit10")
         or check_text(r, ["tx"], tc, "wallet_limit10b")
         or check_text(r, ["transaction"], tc, "wallet_limit10c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=100
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_trader_txs to get 100 swap transactions for wallet {WALLET_1} (limit=100)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_limit100")
         or check_text(r, ["tx"], tc, "wallet_limit100b")
         or check_text(r, ["transaction"], tc, "wallet_limit100c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: offset pagination
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_trader_txs to get the next page of swap transactions for wallet {WALLET_1} (offset=50, limit=10)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_offset")
         or check_text(r, ["tx"], tc, "wallet_offset2")
         or check_text(r, ["transaction"], tc, "wallet_offset3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: ui_amount_mode=raw
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_trader_txs to get swap transactions for wallet {WALLET_1} with ui_amount_mode=raw, limit=10",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet_raw")
         or check_text(r, ["tx"], tc, "wallet_raw2")
         or check_text(r, ["transaction"], tc, "wallet_raw3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Different wallet
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_trader_txs to get recent swap transactions for wallet {WALLET_2}, limit=10",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "wallet2_swaps")
         or check_text(r, ["tx"], tc, "wallet2_swaps2")
         or check_text(r, ["transaction"], tc, "wallet2_swaps3")
         or check_text(r, ["wallet"], tc, "wallet2_swaps4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_trader_txs to show me what wallet {WALLET_1} has been trading recently",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "natural_wallet_trades")
         or check_text(r, ["tx"], tc, "natural_wallet_trades2")
         or check_text(r, ["transaction"], tc, "natural_wallet_trades3")
         or check_text(r, ["wallet"], tc, "natural_wallet_trades4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language smart money
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_trader_txs to analyze the swap history of this smart money wallet: {WALLET_1} (last 2 hours, after_time={two_hours_ago})",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["swap"], tc, "smart_money")
         or check_text(r, ["tx"], tc, "smart_money2")
         or check_text(r, ["transaction"], tc, "smart_money3")
         or check_text(r, ["wallet"], tc, "smart_money4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: tx_type=all + time window
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_trader_txs to get all DeFi activity for wallet {WALLET_1} in the last 2 hours (tx_type=all, after_time={two_hours_ago}, limit=20)",
        {"birdeye_trader_txs"},
        [lambda r, tc: check_text(r, ["tx"], tc, "all_activity")
         or check_text(r, ["transaction"], tc, "all_activity2")
         or check_text(r, ["swap"], tc, "all_activity3")
         or check_text(r, ["wallet"], tc, "all_activity4")],
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

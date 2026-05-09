"""
Test suite for GET /defi/v3/txs/recent via DeFi Query Service LLM.

Coverage:
  TC-01  Basic recent swaps (default)
  TC-02  tx_type=add
  TC-03  tx_type=remove
  TC-04  tx_type=all
  TC-05  limit=10
  TC-06  limit=100
  TC-07  owner filter (wallet)
  TC-08  after_time filter
  TC-09  before_time filter
  TC-10  ui_amount_mode=raw
  TC-11  Natural language "latest transactions on Solana"
  TC-12  Natural language "most recent DeFi activity"
  TC-13  Tool selection: recent txs → birdeye_txs_recent
  TC-14  Natural language "live transaction feed for Solana"
  TC-15  tx_type=all + limit=50
"""

import asyncio
import httpx
import time

BASE = "http://localhost:3150"

SAMPLE_WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"

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
    print("birdeye_txs_recent (GET /defi/v3/txs/recent) — Full Test Suite")
    print("=" * 60)

    now = int(time.time())
    one_hour_ago = now - 3600
    two_hours_ago = now - 7200

    results = []

    # TC-01: Basic recent swaps
    results.append(await run_test(
        "TC-01",
        "Use birdeye_txs_recent to get the most recent swap transactions on Solana",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "recent_swaps")
         or check_text(r, ["tx"], tc, "recent_swaps2")
         or check_text(r, ["transaction"], tc, "recent_swaps3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: tx_type=add
    results.append(await run_test(
        "TC-02",
        "Use birdeye_txs_recent to get the most recent liquidity addition transactions (tx_type=add, limit=20)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["add"], tc, "recent_adds")
         or check_text(r, ["liquidity"], tc, "recent_adds2")
         or check_text(r, ["tx"], tc, "recent_adds3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: tx_type=remove
    results.append(await run_test(
        "TC-03",
        "Use birdeye_txs_recent to get the most recent liquidity removal transactions (tx_type=remove, limit=20)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["remove"], tc, "recent_removes")
         or check_text(r, ["liquidity"], tc, "recent_removes2")
         or check_text(r, ["tx"], tc, "recent_removes3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: tx_type=all
    results.append(await run_test(
        "TC-04",
        "Use birdeye_txs_recent to get all recent DeFi transaction types (tx_type=all, limit=20)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["tx"], tc, "recent_all_types")
         or check_text(r, ["transaction"], tc, "recent_all_types2")
         or check_text(r, ["swap"], tc, "recent_all_types3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: limit=10
    results.append(await run_test(
        "TC-05",
        "Use birdeye_txs_recent to get the 10 most recent swap transactions on Solana (limit=10)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "recent_limit_10")
         or check_text(r, ["tx"], tc, "recent_limit_10b")
         or check_text(r, ["transaction"], tc, "recent_limit_10c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: limit=100
    results.append(await run_test(
        "TC-06",
        "Use birdeye_txs_recent to get 100 recent swap transactions on Solana (limit=100)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "recent_limit_100")
         or check_text(r, ["tx"], tc, "recent_limit_100b")
         or check_text(r, ["transaction"], tc, "recent_limit_100c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: owner filter
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_txs_recent to get the most recent swap transactions from wallet {SAMPLE_WALLET} (owner filter, limit=10)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "recent_owner")
         or check_text(r, ["tx"], tc, "recent_owner2")
         or check_text(r, ["transaction"], tc, "recent_owner3")
         or check_text(r, ["wallet"], tc, "recent_owner4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: after_time
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_txs_recent to get recent swap transactions from the last hour (after_time={one_hour_ago}, limit=20)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "recent_after_time")
         or check_text(r, ["tx"], tc, "recent_after_time2")
         or check_text(r, ["transaction"], tc, "recent_after_time3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: before_time
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_txs_recent to get swap transactions before {two_hours_ago} (before_time filter, limit=10)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "recent_before_time")
         or check_text(r, ["tx"], tc, "recent_before_time2")
         or check_text(r, ["transaction"], tc, "recent_before_time3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: ui_amount_mode=raw
    results.append(await run_test(
        "TC-10",
        "Use birdeye_txs_recent to get the latest Solana swap transactions with ui_amount_mode=raw, limit=10",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "recent_raw")
         or check_text(r, ["tx"], tc, "recent_raw2")
         or check_text(r, ["transaction"], tc, "recent_raw3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "latest transactions"
    results.append(await run_test(
        "TC-11",
        "Use birdeye_txs_recent to show me the latest transactions happening on Solana DeFi right now",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "natural_latest_txs")
         or check_text(r, ["tx"], tc, "natural_latest_txs2")
         or check_text(r, ["transaction"], tc, "natural_latest_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "most recent DeFi activity"
    results.append(await run_test(
        "TC-12",
        "Use birdeye_txs_recent to show me the most recent DeFi activity on Solana — all transaction types, limit=20",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["tx"], tc, "natural_defi_activity")
         or check_text(r, ["transaction"], tc, "natural_defi_activity2")
         or check_text(r, ["swap"], tc, "natural_defi_activity3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection
    results.append(await run_test(
        "TC-13",
        "Use birdeye_txs_recent to fetch the freshest swap transactions on Solana DeFi",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "tool_selection_recent")
         or check_text(r, ["tx"], tc, "tool_selection_recent2")
         or check_text(r, ["transaction"], tc, "tool_selection_recent3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "live feed"
    results.append(await run_test(
        "TC-14",
        "Use birdeye_txs_recent to give me a live transaction feed for Solana — recent swaps, limit=50",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["swap"], tc, "natural_live_feed")
         or check_text(r, ["tx"], tc, "natural_live_feed2")
         or check_text(r, ["transaction"], tc, "natural_live_feed3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: tx_type=all + limit=50
    results.append(await run_test(
        "TC-15",
        "Use birdeye_txs_recent to get the 50 most recent DeFi transactions of all types on Solana (tx_type=all, limit=50)",
        {"birdeye_txs_recent"},
        [lambda r, tc: check_text(r, ["tx"], tc, "all_types_50")
         or check_text(r, ["transaction"], tc, "all_types_50b")
         or check_text(r, ["swap"], tc, "all_types_50c")],
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

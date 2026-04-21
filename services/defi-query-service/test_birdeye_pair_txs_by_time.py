"""
Test suite for GET /defi/txs/pair/seek_by_time via DeFi Query Service LLM.

Coverage:
  TC-01  SOL/USDC pool swaps in the last hour
  TC-02  BONK/SOL pool swaps in the last 2 hours
  TC-03  WIF/SOL pool with after_time only
  TC-04  tx_type=add (liquidity additions)
  TC-05  tx_type=remove (liquidity removals)
  TC-06  tx_type=all
  TC-07  before_time only filter
  TC-08  before_time + after_time window
  TC-09  limit=10
  TC-10  limit=50
  TC-11  offset=50 (pagination)
  TC-12  ui_amount_mode=raw
  TC-13  JUP/USDC pool time-range swaps
  TC-14  RAY/USDC pool swaps (last 30 min)
  TC-15  Natural language "trades in SOL/USDC pool last hour"
"""

import asyncio
import httpx
import time

BASE = "http://localhost:3150"

SOL_USDC_POOL  = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWaS3CDpRZv6d"   # Raydium SOL/USDC
BONK_SOL_POOL  = "8PhnCfgqpgFM7ZJvttGdBVMXHuU4Q23ACxCvvkYnA2Hm"   # Raydium BONK/SOL
JUP_USDC_POOL  = "C1MgLojNLWBKADvu9BHdtgzz1oZX4dZ5zGdGcgvvW8Wz"   # Orca JUP/USDC
WIF_SOL_POOL   = "EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx"   # Raydium WIF/SOL
RAY_USDC_POOL  = "6UmmUiYoBjSrhakAobJw8BvkmJtDVxaeBtbt7rxWo1mg"   # Raydium RAY/USDC

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
    print("birdeye_pair_txs_by_time (GET /defi/txs/pair/seek_by_time) — Full Test Suite")
    print("=" * 60)

    now = int(time.time())
    one_hour_ago    = now - 3600
    two_hours_ago   = now - 7200
    thirty_min_ago  = now - 1800

    results = []

    # TC-01: SOL/USDC pool last hour
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_pair_txs_by_time to get swap transactions in the SOL/USDC pool ({SOL_USDC_POOL}) from the last hour (after_time={one_hour_ago}, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_usdc_hour")
         or check_text(r, ["swap"], tc, "sol_usdc_hour2")
         or check_text(r, ["tx"], tc, "sol_usdc_hour3")
         or check_text(r, ["pool"], tc, "sol_usdc_hour4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK/SOL pool last 2 hours
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_pair_txs_by_time to get swap transactions in the BONK/SOL pool ({BONK_SOL_POOL}) from the last 2 hours (after_time={two_hours_ago}, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_sol_2h")
         or check_text(r, ["swap"], tc, "bonk_sol_2h2")
         or check_text(r, ["tx"], tc, "bonk_sol_2h3")
         or check_text(r, ["pool"], tc, "bonk_sol_2h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF/SOL pool after_time
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_pair_txs_by_time to get swap transactions in the WIF/SOL pool ({WIF_SOL_POOL}) after timestamp {one_hour_ago}, limit=10",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_sol_after")
         or check_text(r, ["swap"], tc, "wif_sol_after2")
         or check_text(r, ["tx"], tc, "wif_sol_after3")
         or check_text(r, ["pool"], tc, "wif_sol_after4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: tx_type=add
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_pair_txs_by_time to get liquidity addition events in the RAY/USDC pool ({RAY_USDC_POOL}) in the last 2 hours (tx_type=add, after_time={two_hours_ago}, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_add_time")
         or check_text(r, ["add"], tc, "ray_add_time2")
         or check_text(r, ["tx"], tc, "ray_add_time3")
         or check_text(r, ["liquidity"], tc, "ray_add_time4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: tx_type=remove
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_pair_txs_by_time to get liquidity removal events in the RAY/USDC pool ({RAY_USDC_POOL}) in the last 2 hours (tx_type=remove, after_time={two_hours_ago}, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_remove_time")
         or check_text(r, ["remove"], tc, "ray_remove_time2")
         or check_text(r, ["tx"], tc, "ray_remove_time3")
         or check_text(r, ["liquidity"], tc, "ray_remove_time4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: tx_type=all
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_pair_txs_by_time to get all transaction types in the JUP/USDC pool ({JUP_USDC_POOL}) in the last 2 hours (tx_type=all, after_time={two_hours_ago}, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_all_time")
         or check_text(r, ["tx"], tc, "jup_all_time2")
         or check_text(r, ["transaction"], tc, "jup_all_time3")
         or check_text(r, ["pool"], tc, "jup_all_time4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: before_time only
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_pair_txs_by_time to get SOL/USDC pool ({SOL_USDC_POOL}) swap transactions before timestamp {one_hour_ago} (before_time filter, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_before")
         or check_text(r, ["swap"], tc, "sol_before2")
         or check_text(r, ["tx"], tc, "sol_before3")
         or check_text(r, ["pool"], tc, "sol_before4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: before_time + after_time window
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_pair_txs_by_time to get BONK/SOL pool ({BONK_SOL_POOL}) swap transactions between {two_hours_ago} and {one_hour_ago} (time window, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_window")
         or check_text(r, ["swap"], tc, "bonk_window2")
         or check_text(r, ["tx"], tc, "bonk_window3")
         or check_text(r, ["pool"], tc, "bonk_window4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=10
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_pair_txs_by_time to get 10 swap transactions from the SOL/USDC pool ({SOL_USDC_POOL}) in the last hour (limit=10, after_time={one_hour_ago})",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_limit10")
         or check_text(r, ["swap"], tc, "sol_limit10b")
         or check_text(r, ["tx"], tc, "sol_limit10c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: limit=50
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_pair_txs_by_time to get 50 swap transactions from the BONK/SOL pool ({BONK_SOL_POOL}) in the last 2 hours (limit=50, after_time={two_hours_ago})",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_limit50")
         or check_text(r, ["swap"], tc, "bonk_limit50b")
         or check_text(r, ["tx"], tc, "bonk_limit50c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: offset pagination
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_pair_txs_by_time to get the next page of SOL/USDC pool ({SOL_USDC_POOL}) swap transactions from the last 2 hours (after_time={two_hours_ago}, offset=50, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_offset")
         or check_text(r, ["swap"], tc, "sol_offset2")
         or check_text(r, ["tx"], tc, "sol_offset3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: ui_amount_mode=raw
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_pair_txs_by_time to get SOL/USDC pool ({SOL_USDC_POOL}) swap transactions from the last hour with ui_amount_mode=raw, limit=10, after_time={one_hour_ago}",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_raw")
         or check_text(r, ["swap"], tc, "sol_raw2")
         or check_text(r, ["tx"], tc, "sol_raw3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: JUP/USDC pool time-range
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_pair_txs_by_time to get JUP/USDC pool ({JUP_USDC_POOL}) swap transactions from the last 2 hours (after_time={two_hours_ago}, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_time")
         or check_text(r, ["swap"], tc, "jup_time2")
         or check_text(r, ["tx"], tc, "jup_time3")
         or check_text(r, ["pool"], tc, "jup_time4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: RAY/USDC pool last 30 min
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_pair_txs_by_time to get RAY/USDC pool ({RAY_USDC_POOL}) swap transactions from the last 30 minutes (after_time={thirty_min_ago}, limit=10)",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_30min")
         or check_text(r, ["swap"], tc, "ray_30min2")
         or check_text(r, ["tx"], tc, "ray_30min3")
         or check_text(r, ["pool"], tc, "ray_30min4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_pair_txs_by_time to show me all trades in the SOL/USDC pool ({SOL_USDC_POOL}) that happened in the last hour (after_time={one_hour_ago})",
        {"birdeye_pair_txs_by_time"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_sol_usdc")
         or check_text(r, ["swap"], tc, "natural_sol_usdc2")
         or check_text(r, ["tx"], tc, "natural_sol_usdc3")
         or check_text(r, ["pool"], tc, "natural_sol_usdc4")],
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

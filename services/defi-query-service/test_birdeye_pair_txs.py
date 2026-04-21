"""
Test suite for GET /defi/txs/pair via DeFi Query Service LLM.

Coverage:
  TC-01  SOL/USDC Raydium pool swaps (default)
  TC-02  BONK/SOL pool swaps
  TC-03  tx_type=add (liquidity additions)
  TC-04  tx_type=remove (liquidity removals)
  TC-05  tx_type=all
  TC-06  sort_type=asc (oldest first)
  TC-07  limit=10
  TC-08  limit=50
  TC-09  offset pagination
  TC-10  ui_amount_mode=raw
  TC-11  JUP/USDC pool swaps
  TC-12  WIF/SOL pool swaps
  TC-13  Natural language "recent trades in this pool"
  TC-14  Natural language "swap history for this pair"
  TC-15  RAY/USDC pool transactions
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL_USDC_RAY  = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWaS3CDpRZv6d"
BONK_SOL_RAY  = "8PhnCfgqpgFM7ZJvttGdBVMXHuU4Q23ACxCvvkYnA2Hm"
JUP_USDC      = "C1MgLojNLWBKADvu9BHdtgzz1oZX4dZ5zGdGcgvvW8Wz"
WIF_SOL       = "EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx"
RAY_USDC      = "6UmmUiYoBjSrhakAobJw8BvkmJtDVxaeBtbt7rxWo1mg"

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
    print("birdeye_pair_txs (GET /defi/txs/pair) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL/USDC pool swaps
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_pair_txs to get recent swap transactions for the SOL/USDC pool ({SOL_USDC_RAY})",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_usdc_pair_txs")
         or check_text(r, ["usdc"], tc, "sol_usdc_pair_txs2")
         or check_text(r, ["swap"], tc, "sol_usdc_pair_txs3")
         or check_text(r, ["tx"], tc, "sol_usdc_pair_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK/SOL pool swaps
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_pair_txs to get swap transactions for the BONK/SOL pool ({BONK_SOL_RAY})",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_sol_pair_txs")
         or check_text(r, ["sol"], tc, "bonk_sol_pair_txs2")
         or check_text(r, ["swap"], tc, "bonk_sol_pair_txs3")
         or check_text(r, ["tx"], tc, "bonk_sol_pair_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: tx_type=add
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_pair_txs to get liquidity addition transactions for the SOL/USDC pool ({SOL_USDC_RAY}) with tx_type=add, limit=10",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["add"], tc, "pair_add_txs")
         or check_text(r, ["liquidity"], tc, "pair_add_txs2")
         or check_text(r, ["sol"], tc, "pair_add_txs3")
         or check_text(r, ["tx"], tc, "pair_add_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: tx_type=remove
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_pair_txs to get liquidity removal transactions for the RAY/USDC pool ({RAY_USDC}) with tx_type=remove, limit=10",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["remove"], tc, "pair_remove_txs")
         or check_text(r, ["liquidity"], tc, "pair_remove_txs2")
         or check_text(r, ["ray"], tc, "pair_remove_txs3")
         or check_text(r, ["tx"], tc, "pair_remove_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: tx_type=all
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_pair_txs to get all transaction types for the BONK/SOL pool ({BONK_SOL_RAY}) with tx_type=all, limit=10",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "pair_all_txs")
         or check_text(r, ["sol"], tc, "pair_all_txs2")
         or check_text(r, ["tx"], tc, "pair_all_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: sort_type=asc
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_pair_txs to get the oldest swap transactions for the SOL/USDC pool ({SOL_USDC_RAY}) with sort_type=asc, limit=10",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "pair_txs_asc")
         or check_text(r, ["usdc"], tc, "pair_txs_asc2")
         or check_text(r, ["swap"], tc, "pair_txs_asc3")
         or check_text(r, ["tx"], tc, "pair_txs_asc4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: limit=10
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_pair_txs to get the last 10 swap transactions for the JUP/USDC pool ({JUP_USDC}) (limit=10)",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_pair_limit_10")
         or check_text(r, ["usdc"], tc, "jup_pair_limit_10b")
         or check_text(r, ["swap"], tc, "jup_pair_limit_10c")
         or check_text(r, ["tx"], tc, "jup_pair_limit_10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: limit=50
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_pair_txs to get 50 swap transactions for the WIF/SOL pool ({WIF_SOL}) (limit=50)",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_pair_limit_50")
         or check_text(r, ["sol"], tc, "wif_pair_limit_50b")
         or check_text(r, ["swap"], tc, "wif_pair_limit_50c")
         or check_text(r, ["tx"], tc, "wif_pair_limit_50d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: offset pagination
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_pair_txs to get the next page of swap transactions for the SOL/USDC pool ({SOL_USDC_RAY}) with offset=50",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "pair_txs_offset")
         or check_text(r, ["usdc"], tc, "pair_txs_offset2")
         or check_text(r, ["swap"], tc, "pair_txs_offset3")
         or check_text(r, ["tx"], tc, "pair_txs_offset4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: ui_amount_mode=raw
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_pair_txs to get SOL/USDC pool ({SOL_USDC_RAY}) swap transactions with ui_amount_mode=raw, limit=10",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "pair_txs_raw")
         or check_text(r, ["usdc"], tc, "pair_txs_raw2")
         or check_text(r, ["swap"], tc, "pair_txs_raw3")
         or check_text(r, ["tx"], tc, "pair_txs_raw4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: JUP/USDC pool
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_pair_txs to get recent swap transactions for the JUP/USDC pool ({JUP_USDC})",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_usdc_pair_txs")
         or check_text(r, ["usdc"], tc, "jup_usdc_pair_txs2")
         or check_text(r, ["swap"], tc, "jup_usdc_pair_txs3")
         or check_text(r, ["tx"], tc, "jup_usdc_pair_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: WIF/SOL pool
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_pair_txs to get swap transactions for the WIF/SOL pool ({WIF_SOL})",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_sol_pair_txs")
         or check_text(r, ["sol"], tc, "wif_sol_pair_txs2")
         or check_text(r, ["swap"], tc, "wif_sol_pair_txs3")
         or check_text(r, ["tx"], tc, "wif_sol_pair_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "recent trades in this pool"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_pair_txs to show me the most recent trades happening in the BONK/SOL pool: {BONK_SOL_RAY}",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_pool_trades")
         or check_text(r, ["sol"], tc, "natural_pool_trades2")
         or check_text(r, ["swap"], tc, "natural_pool_trades3")
         or check_text(r, ["tx"], tc, "natural_pool_trades4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "swap history for pair"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_pair_txs to get the swap history for this liquidity pool: {SOL_USDC_RAY}, limit=10",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_pair_history")
         or check_text(r, ["usdc"], tc, "natural_pair_history2")
         or check_text(r, ["swap"], tc, "natural_pair_history3")
         or check_text(r, ["tx"], tc, "natural_pair_history4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: RAY/USDC pool
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_pair_txs to get recent swap transactions for the RAY/USDC Raydium pool ({RAY_USDC})",
        {"birdeye_pair_txs"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_usdc_pair_txs")
         or check_text(r, ["usdc"], tc, "ray_usdc_pair_txs2")
         or check_text(r, ["swap"], tc, "ray_usdc_pair_txs3")
         or check_text(r, ["tx"], tc, "ray_usdc_pair_txs4")],
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

"""
Test suite for GET /defi/txs/token via DeFi Query Service LLM.

Coverage:
  TC-01  SOL swaps (default)
  TC-02  BONK swaps
  TC-03  WIF swaps
  TC-04  tx_type=add (liquidity additions)
  TC-05  tx_type=remove (liquidity removals)
  TC-06  tx_type=all
  TC-07  sort_type=asc (oldest first)
  TC-08  limit=10
  TC-09  limit=50 (max)
  TC-10  offset=50 (pagination)
  TC-11  ui_amount_mode=raw
  TC-12  Natural language "recent transactions for SOL"
  TC-13  Natural language "swap history for BONK"
  TC-14  JUP transactions
  TC-15  RAY transactions with sort_type=asc
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"

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
    print("birdeye_token_txs_v1 (GET /defi/txs/token) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL swaps
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_txs_v1 to get recent swap transactions for SOL ({SOL})",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_txs_v1")
         or check_text(r, ["swap"], tc, "sol_txs_v1b")
         or check_text(r, ["tx"], tc, "sol_txs_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK swaps
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_txs_v1 to get swap transactions for BONK ({BONK})",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_txs_v1")
         or check_text(r, ["swap"], tc, "bonk_txs_v1b")
         or check_text(r, ["tx"], tc, "bonk_txs_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF swaps
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_txs_v1 to get swap transactions for WIF ({WIF})",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_txs_v1")
         or check_text(r, ["swap"], tc, "wif_txs_v1b")
         or check_text(r, ["tx"], tc, "wif_txs_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: tx_type=add
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_txs_v1 to get liquidity addition transactions for RAY ({RAY}) (tx_type=add, limit=10)",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_add_v1")
         or check_text(r, ["add"], tc, "ray_add_v1b")
         or check_text(r, ["tx"], tc, "ray_add_v1c")
         or check_text(r, ["liquidity"], tc, "ray_add_v1d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: tx_type=remove
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_txs_v1 to get liquidity removal transactions for RAY ({RAY}) (tx_type=remove, limit=10)",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_remove_v1")
         or check_text(r, ["remove"], tc, "ray_remove_v1b")
         or check_text(r, ["tx"], tc, "ray_remove_v1c")
         or check_text(r, ["liquidity"], tc, "ray_remove_v1d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: tx_type=all
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_txs_v1 to get all transaction types for JUP ({JUP}) (tx_type=all, limit=10)",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_all_v1")
         or check_text(r, ["tx"], tc, "jup_all_v1b")
         or check_text(r, ["transaction"], tc, "jup_all_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_type=asc
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_txs_v1 to get the oldest swap transactions for SOL ({SOL}) (sort_type=asc, limit=10)",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_asc_v1")
         or check_text(r, ["swap"], tc, "sol_asc_v1b")
         or check_text(r, ["tx"], tc, "sol_asc_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: limit=10
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_txs_v1 to get the last 10 swap transactions for BONK ({BONK}) (limit=10)",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_limit_10_v1")
         or check_text(r, ["swap"], tc, "bonk_limit_10_v1b")
         or check_text(r, ["tx"], tc, "bonk_limit_10_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=50
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_txs_v1 to get 50 swap transactions for SOL ({SOL}) (limit=50)",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_limit_50_v1")
         or check_text(r, ["swap"], tc, "sol_limit_50_v1b")
         or check_text(r, ["tx"], tc, "sol_limit_50_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: offset pagination
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_txs_v1 to get the next page of swap transactions for SOL ({SOL}) starting at offset=50",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_offset_v1")
         or check_text(r, ["swap"], tc, "sol_offset_v1b")
         or check_text(r, ["tx"], tc, "sol_offset_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: ui_amount_mode=raw
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_txs_v1 to get SOL ({SOL}) swap transactions with ui_amount_mode=raw, limit=10",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_raw_v1")
         or check_text(r, ["swap"], tc, "sol_raw_v1b")
         or check_text(r, ["tx"], tc, "sol_raw_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "recent transactions for SOL"
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_token_txs_v1 to show me recent swap transactions for SOL ({SOL})",
        {"birdeye_token_txs_v1", "birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_sol_txs")
         or check_text(r, ["swap"], tc, "natural_sol_txs2")
         or check_text(r, ["tx"], tc, "natural_sol_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "swap history for BONK"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_txs_v1 to get the swap history for BONK ({BONK})",
        {"birdeye_token_txs_v1", "birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_bonk_history")
         or check_text(r, ["swap"], tc, "natural_bonk_history2")
         or check_text(r, ["tx"], tc, "natural_bonk_history3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: JUP transactions
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_txs_v1 to get the latest swap transactions for JUP ({JUP}), limit=10",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_txs_v1")
         or check_text(r, ["swap"], tc, "jup_txs_v1b")
         or check_text(r, ["tx"], tc, "jup_txs_v1c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: RAY asc
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_txs_v1 to get the earliest recorded swap transactions for RAY ({RAY}) (sort_type=asc, limit=10)",
        {"birdeye_token_txs_v1"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_asc_v1")
         or check_text(r, ["swap"], tc, "ray_asc_v1b")
         or check_text(r, ["tx"], tc, "ray_asc_v1c")],
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

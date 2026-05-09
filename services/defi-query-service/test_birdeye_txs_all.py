"""
Test suite for GET /defi/v3/txs via DeFi Query Service LLM.

Coverage:
  TC-01  Basic all swaps (no token filter)
  TC-02  tx_type=add (liquidity additions)
  TC-03  tx_type=remove (liquidity removals)
  TC-04  source=raydium
  TC-05  source=jupiter
  TC-06  source=orca
  TC-07  source=meteora
  TC-08  owner filter (wallet transactions)
  TC-09  pool_id filter
  TC-10  after_time filter (last hour)
  TC-11  limit=10
  TC-12  sort_type=asc
  TC-13  Natural language "all recent DeFi transactions on Solana"
  TC-14  Natural language "Jupiter swaps in the last hour"
  TC-15  ui_amount_mode=raw
"""

import asyncio
import httpx
import time

BASE = "http://localhost:3150"

SAMPLE_WALLET  = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
SOL_USDC_POOL  = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWaS3CDpRZv6d"

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
    print("birdeye_txs_all (GET /defi/v3/txs) — Full Test Suite")
    print("=" * 60)

    now = int(time.time())
    one_hour_ago = now - 3600

    results = []

    # TC-01: Basic all swaps
    results.append(await run_test(
        "TC-01",
        "Use birdeye_txs_all to get the latest swap transactions across all Solana DeFi",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["swap"], tc, "all_swaps")
         or check_text(r, ["tx"], tc, "all_swaps2")
         or check_text(r, ["transaction"], tc, "all_swaps3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: tx_type=add
    results.append(await run_test(
        "TC-02",
        "Use birdeye_txs_all to get recent liquidity addition transactions (tx_type=add, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["add"], tc, "all_adds")
         or check_text(r, ["liquidity"], tc, "all_adds2")
         or check_text(r, ["tx"], tc, "all_adds3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: tx_type=remove
    results.append(await run_test(
        "TC-03",
        "Use birdeye_txs_all to get recent liquidity removal transactions (tx_type=remove, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["remove"], tc, "all_removes")
         or check_text(r, ["liquidity"], tc, "all_removes2")
         or check_text(r, ["tx"], tc, "all_removes3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: source=raydium
    results.append(await run_test(
        "TC-04",
        "Use birdeye_txs_all to get swap transactions on Raydium (source=raydium, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["raydium"], tc, "raydium_txs")
         or check_text(r, ["swap"], tc, "raydium_txs2")
         or check_text(r, ["tx"], tc, "raydium_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: source=jupiter
    results.append(await run_test(
        "TC-05",
        "Use birdeye_txs_all to get Jupiter swap transactions (source=jupiter, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["jupiter"], tc, "jupiter_txs")
         or check_text(r, ["swap"], tc, "jupiter_txs2")
         or check_text(r, ["tx"], tc, "jupiter_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: source=orca
    results.append(await run_test(
        "TC-06",
        "Use birdeye_txs_all to get Orca DEX swap transactions (source=orca, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["orca"], tc, "orca_txs")
         or check_text(r, ["swap"], tc, "orca_txs2")
         or check_text(r, ["tx"], tc, "orca_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: source=meteora
    results.append(await run_test(
        "TC-07",
        "Use birdeye_txs_all to get Meteora transactions (source=meteora, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["meteora"], tc, "meteora_txs")
         or check_text(r, ["swap"], tc, "meteora_txs2")
         or check_text(r, ["tx"], tc, "meteora_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: owner filter
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_txs_all to get swap transactions from wallet {SAMPLE_WALLET} (owner filter, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["swap"], tc, "owner_txs")
         or check_text(r, ["tx"], tc, "owner_txs2")
         or check_text(r, ["transaction"], tc, "owner_txs3")
         or check_text(r, ["wallet"], tc, "owner_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: pool_id filter
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_txs_all to get transactions in the SOL/USDC pool (pool_id={SOL_USDC_POOL}, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["sol"], tc, "pool_id_txs")
         or check_text(r, ["usdc"], tc, "pool_id_txs2")
         or check_text(r, ["tx"], tc, "pool_id_txs3")
         or check_text(r, ["pool"], tc, "pool_id_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: after_time filter
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_txs_all to get all Solana DeFi swap transactions from the last hour (after_time={one_hour_ago}, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["swap"], tc, "after_time_txs")
         or check_text(r, ["tx"], tc, "after_time_txs2")
         or check_text(r, ["transaction"], tc, "after_time_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: limit=10
    results.append(await run_test(
        "TC-11",
        "Use birdeye_txs_all to get the last 10 swap transactions on Solana DeFi (limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["swap"], tc, "limit_10_txs")
         or check_text(r, ["tx"], tc, "limit_10_txs2")
         or check_text(r, ["transaction"], tc, "limit_10_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: sort_type=asc
    results.append(await run_test(
        "TC-12",
        "Use birdeye_txs_all to get the oldest swap transactions on Solana DeFi (sort_type=asc, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["swap"], tc, "asc_txs")
         or check_text(r, ["tx"], tc, "asc_txs2")
         or check_text(r, ["transaction"], tc, "asc_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "all recent DeFi txs"
    results.append(await run_test(
        "TC-13",
        "Use birdeye_txs_all to show me all recent DeFi swap transactions on Solana, limit=10",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["swap"], tc, "natural_all_defi")
         or check_text(r, ["tx"], tc, "natural_all_defi2")
         or check_text(r, ["transaction"], tc, "natural_all_defi3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "Jupiter swaps last hour"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_txs_all to show me Jupiter swap transactions from the last hour (source=jupiter, after_time={one_hour_ago}, limit=10)",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["jupiter"], tc, "natural_jup_hour")
         or check_text(r, ["swap"], tc, "natural_jup_hour2")
         or check_text(r, ["tx"], tc, "natural_jup_hour3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: ui_amount_mode=raw
    results.append(await run_test(
        "TC-15",
        "Use birdeye_txs_all to get recent Solana swap transactions with ui_amount_mode=raw, limit=10",
        {"birdeye_txs_all"},
        [lambda r, tc: check_text(r, ["swap"], tc, "txs_all_raw")
         or check_text(r, ["tx"], tc, "txs_all_raw2")
         or check_text(r, ["transaction"], tc, "txs_all_raw3")],
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

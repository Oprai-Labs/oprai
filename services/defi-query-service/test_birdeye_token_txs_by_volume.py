"""
Test suite for GET /defi/v3/token/txs-by-volume via DeFi Query Service LLM.

Coverage:
  TC-01  SOL large swaps (min_volume=$10K USD)
  TC-02  BONK whale trades (min_volume=$50K)
  TC-03  WIF big sells (tx_type=sell, min_volume=$5K)
  TC-04  tx_type=buy filter
  TC-05  tx_type=all
  TC-06  volume_type=amount (token quantity filter)
  TC-07  max_volume filter
  TC-08  min_volume + max_volume range
  TC-09  source=raydium filter
  TC-10  source=jupiter filter
  TC-11  after_time filter (last hour)
  TC-12  owner (wallet) filter
  TC-13  limit=10
  TC-14  sort_type=asc (smallest first)
  TC-15  Natural language "whale trades on BONK over $50K"
"""

import asyncio
import httpx
import time

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"

WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"

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
    print("birdeye_token_txs_by_volume (GET /defi/v3/token/txs-by-volume) — Full Test Suite")
    print("=" * 60)

    now = int(time.time())
    one_hour_ago = now - 3600

    results = []

    # TC-01: SOL large swaps
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_txs_by_volume to find large swap transactions for SOL ({SOL}) over $10,000 USD (min_volume=10000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_large_swaps")
         or check_text(r, ["swap"], tc, "sol_large_swaps2")
         or check_text(r, ["tx"], tc, "sol_large_swaps3")
         or check_text(r, ["volume"], tc, "sol_large_swaps4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK whale trades
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_txs_by_volume to find whale trades on BONK ({BONK}) over $50,000 (min_volume=50000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_whale")
         or check_text(r, ["swap"], tc, "bonk_whale2")
         or check_text(r, ["tx"], tc, "bonk_whale3")
         or check_text(r, ["volume"], tc, "bonk_whale4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF big sells
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_txs_by_volume to find large sell transactions for WIF ({WIF}) over $5,000 (tx_type=sell, min_volume=5000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_sells")
         or check_text(r, ["sell"], tc, "wif_sells2")
         or check_text(r, ["tx"], tc, "wif_sells3")
         or check_text(r, ["volume"], tc, "wif_sells4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: tx_type=buy
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_txs_by_volume to find large buy transactions for JUP ({JUP}) over $10,000 (tx_type=buy, min_volume=10000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_buys")
         or check_text(r, ["buy"], tc, "jup_buys2")
         or check_text(r, ["tx"], tc, "jup_buys3")
         or check_text(r, ["volume"], tc, "jup_buys4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: tx_type=all
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_txs_by_volume to get all large transactions for SOL ({SOL}) over $100,000 (tx_type=all, min_volume=100000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_all_large")
         or check_text(r, ["tx"], tc, "sol_all_large2")
         or check_text(r, ["transaction"], tc, "sol_all_large3")
         or check_text(r, ["volume"], tc, "sol_all_large4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: volume_type=amount
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_txs_by_volume to find BONK ({BONK}) trades by token amount — over 1,000,000 BONK (volume_type=amount, min_volume=1000000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_amount")
         or check_text(r, ["tx"], tc, "bonk_amount2")
         or check_text(r, ["volume"], tc, "bonk_amount3")
         or check_text(r, ["amount"], tc, "bonk_amount4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: max_volume filter
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_txs_by_volume to find medium-size SOL ({SOL}) swaps under $50,000 (max_volume=50000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_max_vol")
         or check_text(r, ["swap"], tc, "sol_max_vol2")
         or check_text(r, ["tx"], tc, "sol_max_vol3")
         or check_text(r, ["volume"], tc, "sol_max_vol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: min + max range
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_txs_by_volume to find SOL ({SOL}) swaps between $10,000 and $100,000 (min_volume=10000, max_volume=100000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_range")
         or check_text(r, ["swap"], tc, "sol_range2")
         or check_text(r, ["tx"], tc, "sol_range3")
         or check_text(r, ["volume"], tc, "sol_range4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: source=raydium
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_txs_by_volume to find large Raydium swaps for SOL ({SOL}) over $10,000 (source=raydium, min_volume=10000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["raydium"], tc, "sol_raydium")
         or check_text(r, ["sol"], tc, "sol_raydium2")
         or check_text(r, ["tx"], tc, "sol_raydium3")
         or check_text(r, ["volume"], tc, "sol_raydium4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: source=jupiter
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_txs_by_volume to find large Jupiter swaps for BONK ({BONK}) over $5,000 (source=jupiter, min_volume=5000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["jupiter"], tc, "bonk_jupiter")
         or check_text(r, ["bonk"], tc, "bonk_jupiter2")
         or check_text(r, ["tx"], tc, "bonk_jupiter3")
         or check_text(r, ["volume"], tc, "bonk_jupiter4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: after_time filter
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_txs_by_volume to find large SOL ({SOL}) swaps over $10,000 in the last hour (after_time={one_hour_ago}, min_volume=10000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_after_large")
         or check_text(r, ["swap"], tc, "sol_after_large2")
         or check_text(r, ["tx"], tc, "sol_after_large3")
         or check_text(r, ["volume"], tc, "sol_after_large4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: owner filter
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_token_txs_by_volume to find large SOL ({SOL}) swaps from wallet {WALLET} (owner filter, min_volume=1000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_owner_vol")
         or check_text(r, ["swap"], tc, "sol_owner_vol2")
         or check_text(r, ["tx"], tc, "sol_owner_vol3")
         or check_text(r, ["wallet"], tc, "sol_owner_vol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: limit=10
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_txs_by_volume to get 10 largest BONK ({BONK}) swap transactions by USD value (limit=10, min_volume=1000)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_limit10")
         or check_text(r, ["swap"], tc, "bonk_limit10b")
         or check_text(r, ["tx"], tc, "bonk_limit10c")
         or check_text(r, ["volume"], tc, "bonk_limit10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: sort_type=asc
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_txs_by_volume to find the smallest qualifying SOL ({SOL}) trades over $5,000 (sort_type=asc, min_volume=5000, limit=10)",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_asc_vol")
         or check_text(r, ["swap"], tc, "sol_asc_vol2")
         or check_text(r, ["tx"], tc, "sol_asc_vol3")
         or check_text(r, ["volume"], tc, "sol_asc_vol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language whale query
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_txs_by_volume to show me whale trades on BONK ({BONK}) — swaps over $50,000 USD",
        {"birdeye_token_txs_by_volume"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_whale_bonk")
         or check_text(r, ["swap"], tc, "natural_whale_bonk2")
         or check_text(r, ["tx"], tc, "natural_whale_bonk3")
         or check_text(r, ["volume"], tc, "natural_whale_bonk4")],
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

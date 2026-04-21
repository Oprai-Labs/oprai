"""
Test suite for GET /v1/wallet/tx_list via DeFi Query Service LLM.

Coverage:
  TC-01  Basic transaction history (default)
  TC-02  limit=10
  TC-03  limit=50
  TC-04  limit=100 (max)
  TC-05  ui_amount_mode=raw
  TC-06  ui_amount_mode=scaled (explicit)
  TC-07  chain=solana (explicit)
  TC-08  chain=ethereum
  TC-09  chain=base
  TC-10  Different wallet address
  TC-11  limit=5 (minimal)
  TC-12  Natural language "recent transactions for wallet X"
  TC-13  Natural language "what has this wallet been doing"
  TC-14  Natural language "swap history for address"
  TC-15  limit=20 + ui_amount_mode=raw
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
    print("birdeye_wallet_tx_list (GET /v1/wallet/tx_list) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic tx history
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_tx_list to get the transaction history for wallet {WALLET_1}",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "basic_txs")
         or check_text(r, ["transaction"], tc, "basic_txs2")
         or check_text(r, ["swap"], tc, "basic_txs3")
         or check_text(r, ["wallet"], tc, "basic_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: limit=10
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_tx_list to get the last 10 transactions for wallet {WALLET_1} (limit=10)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "limit10")
         or check_text(r, ["transaction"], tc, "limit10b")
         or check_text(r, ["swap"], tc, "limit10c")
         or check_text(r, ["wallet"], tc, "limit10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: limit=50
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_tx_list to get 50 transactions for wallet {WALLET_1} (limit=50)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "limit50")
         or check_text(r, ["transaction"], tc, "limit50b")
         or check_text(r, ["swap"], tc, "limit50c")
         or check_text(r, ["wallet"], tc, "limit50d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: limit=100
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_tx_list to get 100 transactions for wallet {WALLET_1} (limit=100)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "limit100")
         or check_text(r, ["transaction"], tc, "limit100b")
         or check_text(r, ["swap"], tc, "limit100c")
         or check_text(r, ["wallet"], tc, "limit100d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: ui_amount_mode=raw
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_tx_list to get transactions for wallet {WALLET_1} with raw amounts (ui_amount_mode=raw, limit=10)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "raw_amounts")
         or check_text(r, ["transaction"], tc, "raw_amounts2")
         or check_text(r, ["swap"], tc, "raw_amounts3")
         or check_text(r, ["wallet"], tc, "raw_amounts4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_tx_list to get transactions for wallet {WALLET_1} with scaled amounts (ui_amount_mode=scaled, limit=10)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "scaled_amounts")
         or check_text(r, ["transaction"], tc, "scaled_amounts2")
         or check_text(r, ["swap"], tc, "scaled_amounts3")
         or check_text(r, ["wallet"], tc, "scaled_amounts4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: chain=solana
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_tx_list to get Solana transaction history for wallet {WALLET_1} (chain=solana, limit=10)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "chain_sol")
         or check_text(r, ["transaction"], tc, "chain_sol2")
         or check_text(r, ["swap"], tc, "chain_sol3")
         or check_text(r, ["sol"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: chain=ethereum
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_tx_list to get Ethereum transaction history for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=ethereum, limit=10)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "chain_eth")
         or check_text(r, ["transaction"], tc, "chain_eth2")
         or check_text(r, ["ethereum"], tc, "chain_eth3")
         or check_text(r, ["wallet"], tc, "chain_eth4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: chain=base
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_tx_list to get Base chain transactions for wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 (chain=base, limit=10)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "chain_base")
         or check_text(r, ["transaction"], tc, "chain_base2")
         or check_text(r, ["base"], tc, "chain_base3")
         or check_text(r, ["wallet"], tc, "chain_base4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Different wallet
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_tx_list to get the recent transactions for wallet {WALLET_2}, limit=10",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "wallet2_txs")
         or check_text(r, ["transaction"], tc, "wallet2_txs2")
         or check_text(r, ["swap"], tc, "wallet2_txs3")
         or check_text(r, ["wallet"], tc, "wallet2_txs4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: limit=5
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_tx_list to get the last 5 transactions for wallet {WALLET_1} (limit=5)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "limit5")
         or check_text(r, ["transaction"], tc, "limit5b")
         or check_text(r, ["swap"], tc, "limit5c")
         or check_text(r, ["wallet"], tc, "limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "recent transactions"
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_tx_list to show me the recent transactions for wallet {WALLET_1}",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "natural_recent")
         or check_text(r, ["transaction"], tc, "natural_recent2")
         or check_text(r, ["swap"], tc, "natural_recent3")
         or check_text(r, ["wallet"], tc, "natural_recent4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "what has wallet been doing"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_tx_list to show me what wallet {WALLET_1} has been doing recently",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "natural_activity")
         or check_text(r, ["transaction"], tc, "natural_activity2")
         or check_text(r, ["swap"], tc, "natural_activity3")
         or check_text(r, ["wallet"], tc, "natural_activity4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "swap history"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_tx_list to get the swap and transfer history for address {WALLET_1}",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "natural_swap_hist")
         or check_text(r, ["transaction"], tc, "natural_swap_hist2")
         or check_text(r, ["swap"], tc, "natural_swap_hist3")
         or check_text(r, ["wallet"], tc, "natural_swap_hist4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: limit=20 + raw
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_tx_list to get 20 transactions for wallet {WALLET_1} with raw token amounts (limit=20, ui_amount_mode=raw)",
        {"birdeye_wallet_tx_list"},
        [lambda r, tc: check_text(r, ["tx"], tc, "limit20_raw")
         or check_text(r, ["transaction"], tc, "limit20_raw2")
         or check_text(r, ["swap"], tc, "limit20_raw3")
         or check_text(r, ["wallet"], tc, "limit20_raw4")],
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

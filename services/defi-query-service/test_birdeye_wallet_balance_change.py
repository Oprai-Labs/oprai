"""
Test suite for GET /wallet/v2/balance-change via DeFi Query Service LLM.

Coverage:
  TC-01  Balance change for wallet (default)
  TC-02  Different wallet
  TC-03  type=SOL (native only)
  TC-04  type=SPL (tokens only)
  TC-05  change_type=increase (inflows)
  TC-06  change_type=decrease (outflows)
  TC-07  token_address filter (BONK)
  TC-08  limit=10
  TC-09  limit=100
  TC-10  offset=20 pagination
  TC-11  ui_amount_mode=raw
  TC-12  chain=solana explicit
  TC-13  time_from filter
  TC-14  Natural language "show me balance changes for wallet"
  TC-15  Natural language "what tokens flowed into my wallet"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

WALLET1 = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
WALLET2 = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
BONK    = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

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
    print("birdeye_wallet_balance_change (GET /wallet/v2/balance-change) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Default
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_balance_change to get the balance change history for wallet {WALLET1}",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "default")
         or check_text(r, ["change"], tc, "default2")
         or check_text(r, ["token"], tc, "default3")
         or check_text(r, ["amount"], tc, "default4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Different wallet
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_balance_change to get balance changes for wallet {WALLET2}",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "wallet2")
         or check_text(r, ["change"], tc, "wallet2b")
         or check_text(r, ["token"], tc, "wallet2c")
         or check_text(r, ["amount"], tc, "wallet2d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: type=SOL
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_balance_change to get SOL balance changes for wallet {WALLET1} (type=SOL)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "type_sol")
         or check_text(r, ["sol"], tc, "type_sol2")
         or check_text(r, ["change"], tc, "type_sol3")
         or check_text(r, ["amount"], tc, "type_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: type=SPL
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_balance_change to get SPL token balance changes for wallet {WALLET1} (type=SPL)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "type_spl")
         or check_text(r, ["token"], tc, "type_spl2")
         or check_text(r, ["change"], tc, "type_spl3")
         or check_text(r, ["amount"], tc, "type_spl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: change_type=increase
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_balance_change to get inflow balance changes for wallet {WALLET1} (change_type=increase)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "inflow")
         or check_text(r, ["increase"], tc, "inflow2")
         or check_text(r, ["change"], tc, "inflow3")
         or check_text(r, ["amount"], tc, "inflow4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: change_type=decrease
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_balance_change to get outflow balance changes for wallet {WALLET1} (change_type=decrease)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "outflow")
         or check_text(r, ["decrease"], tc, "outflow2")
         or check_text(r, ["change"], tc, "outflow3")
         or check_text(r, ["amount"], tc, "outflow4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: token_address filter
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_balance_change to get BONK ({BONK}) balance changes for wallet {WALLET1} (token_address={BONK})",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "token_filter")
         or check_text(r, ["bonk"], tc, "token_filter2")
         or check_text(r, ["change"], tc, "token_filter3")
         or check_text(r, ["amount"], tc, "token_filter4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: limit=10
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_balance_change to get the last 10 balance changes for wallet {WALLET1} (limit=10)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "limit10")
         or check_text(r, ["change"], tc, "limit10b")
         or check_text(r, ["token"], tc, "limit10c")
         or check_text(r, ["amount"], tc, "limit10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=100
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_balance_change to get 100 balance changes for wallet {WALLET1} (limit=100)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "limit100")
         or check_text(r, ["change"], tc, "limit100b")
         or check_text(r, ["token"], tc, "limit100c")
         or check_text(r, ["amount"], tc, "limit100d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: offset=20 pagination
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_balance_change to get the next page of balance changes for wallet {WALLET1} (offset=20, limit=10)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "offset20")
         or check_text(r, ["change"], tc, "offset20b")
         or check_text(r, ["token"], tc, "offset20c")
         or check_text(r, ["amount"], tc, "offset20d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: ui_amount_mode=raw
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_balance_change to get balance changes for wallet {WALLET1} with raw amounts (ui_amount_mode=raw, limit=10)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "raw_mode")
         or check_text(r, ["change"], tc, "raw_mode2")
         or check_text(r, ["token"], tc, "raw_mode3")
         or check_text(r, ["amount"], tc, "raw_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: chain=solana explicit
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_balance_change to get Solana balance changes for wallet {WALLET1} (chain=solana, limit=10)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "chain_sol")
         or check_text(r, ["solana"], tc, "chain_sol2")
         or check_text(r, ["change"], tc, "chain_sol3")
         or check_text(r, ["amount"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: time_from filter
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_balance_change to get balance changes for wallet {WALLET1} after time_from=1700000000 (limit=10)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "time_from")
         or check_text(r, ["change"], tc, "time_from2")
         or check_text(r, ["token"], tc, "time_from3")
         or check_text(r, ["amount"], tc, "time_from4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_balance_change to show me the recent balance changes for wallet {WALLET1}",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "natural_changes")
         or check_text(r, ["change"], tc, "natural_changes2")
         or check_text(r, ["token"], tc, "natural_changes3")
         or check_text(r, ["amount"], tc, "natural_changes4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language inflows
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_balance_change to show me what tokens flowed into wallet {WALLET1} (change_type=increase)",
        {"birdeye_wallet_balance_change"},
        [lambda r, tc: check_text(r, ["balance"], tc, "natural_inflow")
         or check_text(r, ["increase"], tc, "natural_inflow2")
         or check_text(r, ["token"], tc, "natural_inflow3")
         or check_text(r, ["amount"], tc, "natural_inflow4")],
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

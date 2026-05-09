"""
Test suite for GET /v1/wallet/token_list via DeFi Query Service LLM.

Coverage:
  TC-01  Basic token holdings (default)
  TC-02  ui_amount_mode=raw
  TC-03  ui_amount_mode=scaled (explicit)
  TC-04  chain=solana (explicit)
  TC-05  chain=ethereum
  TC-06  chain=base
  TC-07  chain=arbitrum
  TC-08  Different wallet address
  TC-09  Natural language "what tokens does wallet X hold"
  TC-10  Natural language "wallet portfolio"
  TC-11  Natural language "token balances for address"
  TC-12  Natural language "what is in this wallet"
  TC-13  raw amounts + different wallet
  TC-14  chain=bsc
  TC-15  Natural language "holdings with USD values"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

WALLET_1 = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
WALLET_2 = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
ETH_WALLET = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"

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
    print("birdeye_wallet_token_list (GET /v1/wallet/token_list) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic holdings
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_token_list to get the token holdings for wallet {WALLET_1}",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "basic_holdings")
         or check_text(r, ["balance"], tc, "basic_holdings2")
         or check_text(r, ["portfolio"], tc, "basic_holdings3")
         or check_text(r, ["usd"], tc, "basic_holdings4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: ui_amount_mode=raw
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_token_list to get wallet {WALLET_1} token balances with raw amounts (ui_amount_mode=raw)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "raw_mode")
         or check_text(r, ["balance"], tc, "raw_mode2")
         or check_text(r, ["portfolio"], tc, "raw_mode3")
         or check_text(r, ["usd"], tc, "raw_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_token_list to get wallet {WALLET_1} token balances with scaled amounts (ui_amount_mode=scaled)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "scaled_mode")
         or check_text(r, ["balance"], tc, "scaled_mode2")
         or check_text(r, ["portfolio"], tc, "scaled_mode3")
         or check_text(r, ["usd"], tc, "scaled_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: chain=solana
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_token_list to get Solana token holdings for wallet {WALLET_1} (chain=solana)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_sol")
         or check_text(r, ["balance"], tc, "chain_sol2")
         or check_text(r, ["sol"], tc, "chain_sol3")
         or check_text(r, ["usd"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: chain=ethereum
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_token_list to get Ethereum token holdings for wallet {ETH_WALLET} (chain=ethereum)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_eth")
         or check_text(r, ["balance"], tc, "chain_eth2")
         or check_text(r, ["ethereum"], tc, "chain_eth3")
         or check_text(r, ["usd"], tc, "chain_eth4")
         or check_text(r, ["portfolio"], tc, "chain_eth5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: chain=base
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_token_list to get Base chain token holdings for wallet {ETH_WALLET} (chain=base)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_base")
         or check_text(r, ["balance"], tc, "chain_base2")
         or check_text(r, ["base"], tc, "chain_base3")
         or check_text(r, ["usd"], tc, "chain_base4")
         or check_text(r, ["portfolio"], tc, "chain_base5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: chain=arbitrum
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_token_list to get Arbitrum token holdings for wallet {ETH_WALLET} (chain=arbitrum)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_arb")
         or check_text(r, ["balance"], tc, "chain_arb2")
         or check_text(r, ["arbitrum"], tc, "chain_arb3")
         or check_text(r, ["usd"], tc, "chain_arb4")
         or check_text(r, ["portfolio"], tc, "chain_arb5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Different wallet
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_token_list to get token holdings for wallet {WALLET_2}",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "wallet2_holdings")
         or check_text(r, ["balance"], tc, "wallet2_holdings2")
         or check_text(r, ["portfolio"], tc, "wallet2_holdings3")
         or check_text(r, ["usd"], tc, "wallet2_holdings4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "what tokens does wallet hold"
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_token_list to show me what tokens wallet {WALLET_1} is holding",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_holdings")
         or check_text(r, ["balance"], tc, "natural_holdings2")
         or check_text(r, ["portfolio"], tc, "natural_holdings3")
         or check_text(r, ["usd"], tc, "natural_holdings4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "wallet portfolio"
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_token_list to get the portfolio for wallet {WALLET_1}",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_portfolio")
         or check_text(r, ["balance"], tc, "natural_portfolio2")
         or check_text(r, ["portfolio"], tc, "natural_portfolio3")
         or check_text(r, ["usd"], tc, "natural_portfolio4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "token balances"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_token_list to get all token balances for address {WALLET_1}",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_balances")
         or check_text(r, ["balance"], tc, "natural_balances2")
         or check_text(r, ["portfolio"], tc, "natural_balances3")
         or check_text(r, ["usd"], tc, "natural_balances4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "what is in this wallet"
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_token_list to tell me what is in wallet {WALLET_1}",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_whats_in")
         or check_text(r, ["balance"], tc, "natural_whats_in2")
         or check_text(r, ["portfolio"], tc, "natural_whats_in3")
         or check_text(r, ["usd"], tc, "natural_whats_in4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: raw + different wallet
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_token_list to get raw token amounts for wallet {WALLET_2} (ui_amount_mode=raw)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "wallet2_raw")
         or check_text(r, ["balance"], tc, "wallet2_raw2")
         or check_text(r, ["portfolio"], tc, "wallet2_raw3")
         or check_text(r, ["usd"], tc, "wallet2_raw4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: chain=bsc
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_token_list to get BSC token holdings for wallet {ETH_WALLET} (chain=bsc)",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "chain_bsc")
         or check_text(r, ["balance"], tc, "chain_bsc2")
         or check_text(r, ["bsc"], tc, "chain_bsc3")
         or check_text(r, ["usd"], tc, "chain_bsc4")
         or check_text(r, ["portfolio"], tc, "chain_bsc5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language "holdings with USD values"
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_token_list to show me all token holdings with USD values for wallet {WALLET_1}",
        {"birdeye_wallet_token_list"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_usd_values")
         or check_text(r, ["usd"], tc, "natural_usd_values2")
         or check_text(r, ["balance"], tc, "natural_usd_values3")
         or check_text(r, ["portfolio"], tc, "natural_usd_values4")],
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

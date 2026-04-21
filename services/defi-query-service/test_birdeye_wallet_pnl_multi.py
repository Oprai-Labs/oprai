"""
Test suite for GET /wallet/v2/pnl/multiple via DeFi Query Service LLM.

Coverage:
  TC-01  Two wallets on SOL
  TC-02  Two wallets on BONK
  TC-03  Two wallets on WIF
  TC-04  Three wallets on JUP
  TC-05  Three wallets on RAY
  TC-06  chain=solana (explicit)
  TC-07  Single wallet on SOL
  TC-08  Two wallets on BONK (reverse order)
  TC-09  Different wallet pair on SOL
  TC-10  Two wallets on WIF (chain=solana)
  TC-11  Natural language "compare PnL of two wallets on BONK"
  TC-12  Natural language "who made more money trading SOL"
  TC-13  Natural language "leaderboard for JUP traders"
  TC-14  Three wallets on WIF
  TC-15  Two wallets on RAY
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

WALLET_1 = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
WALLET_2 = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
WALLET_3 = "GThUX1Atko4tqhN2NaiTazFAcaPBFoGJMJ9RB8ySBLDa"

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
    print("birdeye_wallet_pnl_multi (GET /wallet/v2/pnl/multiple) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Two wallets on SOL
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_pnl_multi to compare the SOL ({SOL}) PnL for wallets {WALLET_1} and {WALLET_2}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "two_wallets_sol")
         or check_text(r, ["profit"], tc, "two_wallets_sol2")
         or check_text(r, ["sol"], tc, "two_wallets_sol3")
         or check_text(r, ["wallet"], tc, "two_wallets_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Two wallets on BONK
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_pnl_multi to compare BONK ({BONK}) trading PnL for wallets {WALLET_1} and {WALLET_2}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "two_wallets_bonk")
         or check_text(r, ["profit"], tc, "two_wallets_bonk2")
         or check_text(r, ["bonk"], tc, "two_wallets_bonk3")
         or check_text(r, ["wallet"], tc, "two_wallets_bonk4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Two wallets on WIF
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_pnl_multi to compare WIF ({WIF}) PnL for wallets {WALLET_1} and {WALLET_2}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "two_wallets_wif")
         or check_text(r, ["profit"], tc, "two_wallets_wif2")
         or check_text(r, ["wif"], tc, "two_wallets_wif3")
         or check_text(r, ["wallet"], tc, "two_wallets_wif4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Three wallets on JUP
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_pnl_multi to compare JUP ({JUP}) trading performance for wallets {WALLET_1}, {WALLET_2}, and {WALLET_3}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "three_wallets_jup")
         or check_text(r, ["profit"], tc, "three_wallets_jup2")
         or check_text(r, ["jup"], tc, "three_wallets_jup3")
         or check_text(r, ["wallet"], tc, "three_wallets_jup4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Three wallets on RAY
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_pnl_multi to get RAY ({RAY}) PnL for wallets {WALLET_1}, {WALLET_2}, and {WALLET_3}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "three_wallets_ray")
         or check_text(r, ["profit"], tc, "three_wallets_ray2")
         or check_text(r, ["ray"], tc, "three_wallets_ray3")
         or check_text(r, ["wallet"], tc, "three_wallets_ray4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: chain=solana explicit
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_wallet_pnl_multi to compare SOL ({SOL}) PnL on Solana for wallets {WALLET_1} and {WALLET_2} (chain=solana)",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "chain_sol")
         or check_text(r, ["profit"], tc, "chain_sol2")
         or check_text(r, ["sol"], tc, "chain_sol3")
         or check_text(r, ["wallet"], tc, "chain_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Single wallet on SOL
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_wallet_pnl_multi to get the SOL ({SOL}) PnL for wallet {WALLET_1} (single wallet)",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "single_wallet")
         or check_text(r, ["profit"], tc, "single_wallet2")
         or check_text(r, ["sol"], tc, "single_wallet3")
         or check_text(r, ["wallet"], tc, "single_wallet4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Two wallets on BONK (reverse order)
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_wallet_pnl_multi to get BONK ({BONK}) trading results for wallets {WALLET_2} and {WALLET_1}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "bonk_reverse")
         or check_text(r, ["profit"], tc, "bonk_reverse2")
         or check_text(r, ["bonk"], tc, "bonk_reverse3")
         or check_text(r, ["wallet"], tc, "bonk_reverse4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Different wallet pair on SOL
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_wallet_pnl_multi to compare SOL ({SOL}) PnL between wallets {WALLET_2} and {WALLET_3}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "diff_pair_sol")
         or check_text(r, ["profit"], tc, "diff_pair_sol2")
         or check_text(r, ["sol"], tc, "diff_pair_sol3")
         or check_text(r, ["wallet"], tc, "diff_pair_sol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Two wallets on WIF with chain
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_wallet_pnl_multi to compare WIF ({WIF}) trading results for wallets {WALLET_1} and {WALLET_3} (chain=solana)",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "wif_chain")
         or check_text(r, ["profit"], tc, "wif_chain2")
         or check_text(r, ["wif"], tc, "wif_chain3")
         or check_text(r, ["wallet"], tc, "wif_chain4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "compare PnL"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_wallet_pnl_multi to compare the BONK ({BONK}) trading PnL between wallets {WALLET_1} and {WALLET_2} — who made more?",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_compare")
         or check_text(r, ["profit"], tc, "natural_compare2")
         or check_text(r, ["bonk"], tc, "natural_compare3")
         or check_text(r, ["wallet"], tc, "natural_compare4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "who made more"
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_wallet_pnl_multi to find out who made more money trading SOL ({SOL}) — wallet {WALLET_1} or {WALLET_2}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_who_more")
         or check_text(r, ["profit"], tc, "natural_who_more2")
         or check_text(r, ["sol"], tc, "natural_who_more3")
         or check_text(r, ["wallet"], tc, "natural_who_more4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "leaderboard"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_wallet_pnl_multi to create a leaderboard of JUP ({JUP}) traders: wallets {WALLET_1}, {WALLET_2}, {WALLET_3}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "natural_leaderboard")
         or check_text(r, ["profit"], tc, "natural_leaderboard2")
         or check_text(r, ["jup"], tc, "natural_leaderboard3")
         or check_text(r, ["wallet"], tc, "natural_leaderboard4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Three wallets on WIF
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_wallet_pnl_multi to get WIF ({WIF}) PnL for three wallets: {WALLET_1}, {WALLET_2}, {WALLET_3}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "three_wif")
         or check_text(r, ["profit"], tc, "three_wif2")
         or check_text(r, ["wif"], tc, "three_wif3")
         or check_text(r, ["wallet"], tc, "three_wif4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Two wallets on RAY
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_wallet_pnl_multi to compare RAY ({RAY}) trading performance between wallets {WALLET_1} and {WALLET_2}",
        {"birdeye_wallet_pnl_multi"},
        [lambda r, tc: check_text(r, ["pnl"], tc, "two_ray")
         or check_text(r, ["profit"], tc, "two_ray2")
         or check_text(r, ["ray"], tc, "two_ray3")
         or check_text(r, ["wallet"], tc, "two_ray4")],
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

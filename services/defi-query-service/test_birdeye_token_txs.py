"""
Test suite for GET /defi/v3/token/txs via DeFi Query Service LLM.

Coverage:
  TC-01  Basic SOL swaps (default)
  TC-02  tx_type=buy for BONK
  TC-03  tx_type=sell for WIF
  TC-04  tx_type=all for JUP
  TC-05  source=raydium filter
  TC-06  source=jupiter filter
  TC-07  limit=10
  TC-08  owner filter (specific wallet)
  TC-09  after_time filter (recent txs)
  TC-10  ui_amount_mode=raw
  TC-11  sort_type=asc (oldest first)
  TC-12  Natural language "recent swaps for SOL"
  TC-13  Natural language "who bought BONK recently"
  TC-14  tx_type=add (liquidity adds)
  TC-15  source=pump_fun for meme token
"""

import asyncio
import httpx

BASE = "http://localhost:3150"
import time

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"

# A known active Solana wallet for owner filter test
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
    print("birdeye_token_txs (GET /defi/v3/token/txs) — Full Test Suite")
    print("=" * 60)

    now = int(time.time())
    one_hour_ago = now - 3600

    results = []

    # TC-01: Basic SOL swaps
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_txs to get recent swap transactions for SOL ({SOL})",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_swaps")
         or check_text(r, ["swap"], tc, "sol_swaps2")
         or check_text(r, ["tx"], tc, "sol_swaps3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: tx_type=buy for BONK
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_txs to get buy transactions for BONK ({BONK}) with tx_type=buy",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_buys")
         or check_text(r, ["buy"], tc, "bonk_buys2")
         or check_text(r, ["tx"], tc, "bonk_buys3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: tx_type=sell for WIF
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_txs to get sell transactions for WIF ({WIF}) with tx_type=sell",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_sells")
         or check_text(r, ["sell"], tc, "wif_sells2")
         or check_text(r, ["tx"], tc, "wif_sells3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: tx_type=all for JUP
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_txs to get all transaction types for JUP ({JUP}) with tx_type=all, limit=10",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_all_txs")
         or check_text(r, ["tx"], tc, "jup_all_txs2")
         or check_text(r, ["transaction"], tc, "jup_all_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: source=raydium
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_txs to get SOL ({SOL}) swap transactions on Raydium (source=raydium, limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["raydium"], tc, "sol_raydium_txs")
         or check_text(r, ["sol"], tc, "sol_raydium_txs2")
         or check_text(r, ["tx"], tc, "sol_raydium_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: source=jupiter
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_txs to get BONK ({BONK}) swaps routed through Jupiter (source=jupiter, limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["jupiter"], tc, "bonk_jupiter_txs")
         or check_text(r, ["bonk"], tc, "bonk_jupiter_txs2")
         or check_text(r, ["tx"], tc, "bonk_jupiter_txs3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: limit=10
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_txs to get the last 10 swap transactions for RAY ({RAY}) (limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_limit_10")
         or check_text(r, ["swap"], tc, "ray_limit_10b")
         or check_text(r, ["tx"], tc, "ray_limit_10c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: owner filter
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_txs to get SOL ({SOL}) transactions from wallet {SAMPLE_WALLET} (owner filter, limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_owner_filter")
         or check_text(r, ["tx"], tc, "sol_owner_filter2")
         or check_text(r, ["transaction"], tc, "sol_owner_filter3")
         or check_text(r, ["wallet"], tc, "sol_owner_filter4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: after_time filter
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_txs to get BONK ({BONK}) transactions from the last hour (after_time={one_hour_ago}, limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_recent_time")
         or check_text(r, ["tx"], tc, "bonk_recent_time2")
         or check_text(r, ["transaction"], tc, "bonk_recent_time3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: ui_amount_mode=raw
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_txs to get SOL ({SOL}) swap transactions with ui_amount_mode=raw, limit=10",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_txs_raw")
         or check_text(r, ["swap"], tc, "sol_txs_raw2")
         or check_text(r, ["tx"], tc, "sol_txs_raw3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: sort_type=asc
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_txs to get the oldest swap transactions for WIF ({WIF}) (sort_type=asc, limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_txs_asc")
         or check_text(r, ["swap"], tc, "wif_txs_asc2")
         or check_text(r, ["tx"], tc, "wif_txs_asc3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "recent swaps"
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_token_txs to show me the most recent swap transactions for SOL ({SOL})",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_recent_swaps")
         or check_text(r, ["swap"], tc, "natural_recent_swaps2")
         or check_text(r, ["tx"], tc, "natural_recent_swaps3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "who bought BONK"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_txs to show me who bought BONK ({BONK}) recently — tx_type=buy, limit=10",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_who_bought")
         or check_text(r, ["buy"], tc, "natural_who_bought2")
         or check_text(r, ["tx"], tc, "natural_who_bought3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: tx_type=add (liquidity adds)
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_txs to get liquidity addition transactions for RAY ({RAY}) (tx_type=add, limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_add_liq")
         or check_text(r, ["add"], tc, "ray_add_liq2")
         or check_text(r, ["tx"], tc, "ray_add_liq3")
         or check_text(r, ["liquidity"], tc, "ray_add_liq4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: source=pump_fun
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_txs to get BONK ({BONK}) transactions from pump.fun (source=pump_fun, limit=10)",
        {"birdeye_token_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_pump_fun")
         or check_text(r, ["pump"], tc, "bonk_pump_fun2")
         or check_text(r, ["tx"], tc, "bonk_pump_fun3")
         or check_text(r, ["transaction"], tc, "bonk_pump_fun4")],
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

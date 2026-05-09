"""
Tests for POST /defi/v3/all-time/trades/multiple via DeFi Query Service LLM.

TC-01  BONK + SOL 24h comparison
TC-02  WIF + JUP 7d comparison
TC-03  Three tokens 30d
TC-04  time_frame=alltime
TC-05  time_frame=1h
TC-06  time_frame=1y
TC-07  ui_amount_mode=raw
TC-08  chain=ethereum (two ERC-20 tokens)
TC-09  Four tokens 24h batch
TC-10  Natural language "compare trade counts for BONK and WIF"
TC-11  Natural language "all-time trade volumes for SOL and JUP"
TC-12  Natural language "which has more trades, BONK or SOL"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL   = "So11111111111111111111111111111111111111112"
BONK  = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF   = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP   = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY   = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
USDC_ETH = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT_ETH = "0xdac17f958d2ee523a2206206994597c13d831ec7"

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
    print("birdeye_all_time_trades_multi (POST /defi/v3/all-time/trades/multiple) — Tests")
    print("=" * 60)

    results = []

    # TC-01: BONK + SOL 24h
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_all_time_trades_multi to compare trade stats for BONK ({BONK}) and SOL ({SOL}) over 24h (time_frame=24h)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "bonk_sol_24h")
         or check_text(r, ["bonk"], tc, "bonk_sol_24h2")
         or check_text(r, ["sol"], tc, "bonk_sol_24h3")
         or check_text(r, ["volume"], tc, "bonk_sol_24h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: WIF + JUP 7d
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_all_time_trades_multi to compare trade stats for WIF ({WIF}) and JUP ({JUP}) over 7 days (time_frame=7d)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "wif_jup_7d")
         or check_text(r, ["wif"], tc, "wif_jup_7d2")
         or check_text(r, ["jup"], tc, "wif_jup_7d3")
         or check_text(r, ["volume"], tc, "wif_jup_7d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Three tokens 30d
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_all_time_trades_multi to get 30-day trade stats for BONK ({BONK}), WIF ({WIF}), and JUP ({JUP}) (time_frame=30d)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "three_30d")
         or check_text(r, ["bonk"], tc, "three_30d2")
         or check_text(r, ["volume"], tc, "three_30d3")
         or check_text(r, ["count"], tc, "three_30d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: alltime
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_all_time_trades_multi to get all-time trade stats for BONK ({BONK}) and SOL ({SOL}) (time_frame=alltime)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "alltime")
         or check_text(r, ["bonk"], tc, "alltime2")
         or check_text(r, ["all"], tc, "alltime3")
         or check_text(r, ["volume"], tc, "alltime4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: 1h
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_all_time_trades_multi to get 1-hour trade stats for SOL ({SOL}) and JUP ({JUP}) (time_frame=1h)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "1h")
         or check_text(r, ["sol"], tc, "1h2")
         or check_text(r, ["jup"], tc, "1h3")
         or check_text(r, ["volume"], tc, "1h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: 1y
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_all_time_trades_multi to get 1-year trade stats for WIF ({WIF}) and RAY ({RAY}) (time_frame=1y)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "1y")
         or check_text(r, ["wif"], tc, "1y2")
         or check_text(r, ["ray"], tc, "1y3")
         or check_text(r, ["volume"], tc, "1y4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: ui_amount_mode=raw
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_all_time_trades_multi to get 24h trade stats for BONK ({BONK}) and WIF ({WIF}) with raw amounts (ui_amount_mode=raw)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "raw_mode")
         or check_text(r, ["bonk"], tc, "raw_mode2")
         or check_text(r, ["volume"], tc, "raw_mode3")
         or check_text(r, ["count"], tc, "raw_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: chain=ethereum
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_all_time_trades_multi to get 24h trade stats for {USDC_ETH} and {USDT_ETH} on Ethereum (chain=ethereum, time_frame=24h)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "eth_chain")
         or check_text(r, ["ethereum"], tc, "eth_chain2")
         or check_text(r, ["volume"], tc, "eth_chain3")
         or check_text(r, ["count"], tc, "eth_chain4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Four tokens batch
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_all_time_trades_multi to batch-fetch 24h trade stats for BONK ({BONK}), SOL ({SOL}), WIF ({WIF}), and JUP ({JUP})",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "four_batch")
         or check_text(r, ["bonk"], tc, "four_batch2")
         or check_text(r, ["volume"], tc, "four_batch3")
         or check_text(r, ["count"], tc, "four_batch4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language compare trade counts
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_all_time_trades_multi to compare total trade counts for BONK ({BONK}) and WIF ({WIF}) over the last 24 hours",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "natural_compare")
         or check_text(r, ["bonk"], tc, "natural_compare2")
         or check_text(r, ["wif"], tc, "natural_compare3")
         or check_text(r, ["count"], tc, "natural_compare4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language all-time volumes
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_all_time_trades_multi to get the all-time trading volumes for SOL ({SOL}) and JUP ({JUP})",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["volume"], tc, "natural_alltime_vol")
         or check_text(r, ["trade"], tc, "natural_alltime_vol2")
         or check_text(r, ["sol"], tc, "natural_alltime_vol3")
         or check_text(r, ["jup"], tc, "natural_alltime_vol4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language which has more trades
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_all_time_trades_multi to find out which token has more trades: BONK ({BONK}) or SOL ({SOL}) (time_frame=24h)",
        {"birdeye_all_time_trades_multi"},
        [lambda r, tc: check_text(r, ["trade"], tc, "natural_more")
         or check_text(r, ["bonk"], tc, "natural_more2")
         or check_text(r, ["sol"], tc, "natural_more3")
         or check_text(r, ["count"], tc, "natural_more4")],
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

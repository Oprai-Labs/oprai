"""
Test suite for GET /defi/v3/token/trade-data/multiple via DeFi Query Service LLM.

Coverage:
  TC-01  Two tokens (SOL + BONK)
  TC-02  Three tokens (SOL + BONK + WIF)
  TC-03  Five tokens batch
  TC-04  Single token via multi endpoint (edge case)
  TC-05  frames=1h for batch
  TC-06  frames=1h,4h,24h for batch
  TC-07  ui_amount_mode=raw on batch
  TC-08  ui_amount_mode=scaled on batch
  TC-09  Mixed tokens (SOL + JUP + RAY)
  TC-10  Natural language "compare buy/sell for SOL and BONK"
  TC-11  Natural language "which token has more traders"
  TC-12  Tool selection: batch trade count → birdeye_token_trade_data_multi
  TC-13  Tool selection: batch buy/sell comparison → birdeye_token_trade_data_multi
  TC-14  Large batch (5 tokens) with frames
  TC-15  frames + ui_amount_mode combo on batch
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
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
    print("birdeye_token_trade_data_multi (GET /defi/v3/token/trade-data/multiple) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Two tokens
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_trade_data_multi to get trade data for SOL and BONK: {SOL},{BONK}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "two_token_trade_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Three tokens
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_trade_data_multi to get trade data for SOL, BONK, and WIF: {SOL},{BONK},{WIF}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "three_token_trade_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Five tokens batch
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_trade_data_multi to batch-fetch trade data for 5 tokens: {SOL},{BONK},{WIF},{JUP},{RAY}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "five_token_trade_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Single token via multi (edge case)
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_trade_data_multi with just one address to get JUP ({JUP}) trade data",
        {"birdeye_token_trade_data_multi", "birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["jup"], tc, "single_via_multi")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: frames=1h for batch
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_trade_data_multi to get SOL and BONK trade data with frames=1h: {SOL},{BONK}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "batch_1h_frame")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: frames=1h,4h,24h for batch
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_trade_data_multi for WIF and JUP with frames=1h,4h,24h: {WIF},{JUP}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["wif"], tc, "batch_multi_frames")
         or check_text(r, ["jup"], tc, "batch_multi_frames2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: ui_amount_mode=raw
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_trade_data_multi for SOL and BONK with ui_amount_mode=raw: {SOL},{BONK}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "batch_trade_raw")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_trade_data_multi for WIF and RAY with ui_amount_mode=scaled: {WIF},{RAY}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["wif"], tc, "batch_trade_scaled")
         or check_text(r, ["ray"], tc, "batch_trade_scaled2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Mixed tokens
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_trade_data_multi to get trade activity for SOL, JUP, and RAY: {SOL},{JUP},{RAY}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_jup_ray_trade")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "compare buy/sell for SOL and BONK"
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_trade_data_multi to compare buy and sell activity for SOL and BONK: {SOL},{BONK}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_compare_buy_sell")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "which token has more traders"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_trade_data_multi to find which of WIF or JUP has more unique traders: {WIF},{JUP}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["wif"], tc, "natural_more_traders")
         or check_text(r, ["jup"], tc, "natural_more_traders2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: batch trade count
    results.append(await run_test(
        "TC-12",
        f"I need the trade counts and buy/sell volumes for these three tokens in one API call: {SOL},{BONK},{WIF}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "batch_trade_count_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection: batch buy/sell comparison
    results.append(await run_test(
        "TC-13",
        f"Compare the buy volume vs sell volume for JUP and RAY in one call: {JUP},{RAY}",
        {"birdeye_token_trade_data_multi", "birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["jup"], tc, "batch_buy_sell_comparison")
         or check_text(r, ["ray"], tc, "batch_buy_sell_comparison2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Large batch with frames
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_trade_data_multi for 5 tokens with frames=1h,24h: {SOL},{USDC},{BONK},{WIF},{JUP}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "large_batch_with_frames")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: frames + ui_amount_mode combo
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_trade_data_multi for SOL and BONK with frames=1h,4h,24h and ui_amount_mode=raw: {SOL},{BONK}",
        {"birdeye_token_trade_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "combo_frames_ui_mode")],
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

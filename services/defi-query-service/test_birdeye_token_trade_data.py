"""
Test suite for GET /defi/v3/token/trade-data/single via DeFi Query Service LLM.

Coverage:
  TC-01  Basic SOL trade data (no frames)
  TC-02  frames=1h for BONK
  TC-03  frames=1h,4h,24h for WIF
  TC-04  frames=4h,8h for JUP
  TC-05  ui_amount_mode=raw
  TC-06  ui_amount_mode=scaled
  TC-07  frames with minute intervals (15m,1h)
  TC-08  frames=2h,8h for RAY
  TC-09  Natural language "buy vs sell activity for BONK"
  TC-10  Natural language "how many unique buyers for SOL today"
  TC-11  Natural language "trading momentum for WIF across timeframes"
  TC-12  Tool selection: trade count query → birdeye_token_trade_data
  TC-13  Tool selection: buy/sell volume → birdeye_token_trade_data
  TC-14  frames=24h only
  TC-15  frames=1h,4h,24h + ui_amount_mode=raw combo
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
    print("birdeye_token_trade_data (GET /defi/v3/token/trade-data/single) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic SOL trade data
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_trade_data to get trade data for SOL ({SOL})",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_trade_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: frames=1h for BONK
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_trade_data to get BONK ({BONK}) trade data with frames=1h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_1h_trade")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: frames=1h,4h,24h for WIF
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_trade_data to get WIF ({WIF}) trade activity with frames=1h,4h,24h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_multi_frame_trade")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: frames=4h,8h for JUP
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_trade_data for JUP ({JUP}) with frames=4h,8h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_4h_8h_trade")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: ui_amount_mode=raw
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_trade_data for SOL ({SOL}) with ui_amount_mode=raw",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_trade_raw")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_trade_data for BONK ({BONK}) with ui_amount_mode=scaled",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_trade_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: minute interval frames
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_trade_data for SOL ({SOL}) with frames=15m,1h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_minute_frames")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: frames=2h,8h for RAY
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_trade_data to get RAY ({RAY}) buy/sell counts with frames=2h,8h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_2h_8h_trade")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "buy vs sell activity for BONK"
    results.append(await run_test(
        "TC-09",
        f"What is the buy vs sell activity and volume for BONK ({BONK}) over the last hour?",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_buy_sell")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "unique buyers for SOL"
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_trade_data to show how many unique buyers and sellers SOL ({SOL}) had with frames=1h,24h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_unique_buyers")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "trading momentum for WIF"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_trade_data to analyze trading momentum for WIF ({WIF}) with frames=1h,4h,24h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["wif"], tc, "natural_momentum")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: trade count query
    results.append(await run_test(
        "TC-12",
        f"How many trades happened for SOL ({SOL}) in the last 4 hours?",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["sol"], tc, "trade_count_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection: buy/sell volume
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_trade_data to check the buy volume vs sell volume for BONK ({BONK})",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "buy_sell_volume_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: frames=24h only
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_trade_data to get JUP ({JUP}) trade data with frames=24h",
        {"birdeye_token_trade_data"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_24h_trade")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: frames + ui_amount_mode combo
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_trade_data for SOL ({SOL}) with frames=1h,4h,24h and ui_amount_mode=raw",
        {"birdeye_token_trade_data"},
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

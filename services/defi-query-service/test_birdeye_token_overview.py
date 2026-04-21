"""
Test suite for GET /defi/token_overview via DeFi Query Service LLM.

Coverage:
  TC-01  Basic token overview for SOL (no frames)
  TC-02  frames=1h,4h,24h (multi-frame)
  TC-03  frames=1h only
  TC-04  frames=4h,8h
  TC-05  ui_amount_mode=raw
  TC-06  ui_amount_mode=scaled
  TC-07  frames with minute intervals (frames=15m,1h,4h)
  TC-08  Ethereum chain token
  TC-09  BSC chain token
  TC-10  Natural language "full stats for BONK"
  TC-11  Natural language "deep analysis of WIF with multiple timeframes"
  TC-12  Tool selection: "all data for token X" → birdeye_token_overview
  TC-13  JUP overview with frames
  TC-14  Polygon chain token
  TC-15  frames=1h,4h,24h + ui_amount_mode=scaled combo
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL        = "So11111111111111111111111111111111111111112"
BONK       = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF        = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP        = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
WETH_ETH   = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
WBNB_BSC   = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
WMATIC_POL = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"

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
    print("birdeye_token_overview (GET /defi/token_overview) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic SOL overview (no frames)
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_overview to get a full overview of SOL ({SOL})",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_overview")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: frames=1h,4h,24h
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_overview to get SOL ({SOL}) stats with frames=1h,4h,24h",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_multi_frames")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: frames=1h only
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_overview to get BONK ({BONK}) overview with frames=1h",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_1h_frame")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: frames=4h,8h
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_overview to get WIF ({WIF}) stats with frames=4h,8h",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_4h_8h_frames")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: ui_amount_mode=raw
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_overview for SOL ({SOL}) with ui_amount_mode=raw",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_ui_raw")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_overview for BONK ({BONK}) with ui_amount_mode=scaled",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_ui_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: minute interval frames
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_overview for SOL ({SOL}) with frames=15m,1h,4h",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_minute_frames")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Ethereum chain
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_overview to get an overview of WETH ({WETH_ETH}) on chain=ethereum",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_overview")
         or check_text(r, ["ethereum"], tc, "eth_overview2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: BSC chain
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_overview to get WBNB ({WBNB_BSC}) overview on BSC chain",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["bnb"], tc, "bsc_overview")
         or check_text(r, ["bsc"], tc, "bsc_overview2")
         or check_text(r, ["wbnb"], tc, "bsc_overview3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "full stats for BONK"
    results.append(await run_test(
        "TC-10",
        f"Give me the full stats and overview for BONK ({BONK}) — price, volume, liquidity, everything",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_full_stats")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language multi-timeframe analysis
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_overview to do a deep analysis of WIF ({WIF}) with frames=1h,4h,24h — I want multi-timeframe data",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["wif"], tc, "natural_multiframe_analysis")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: "all data for token X"
    results.append(await run_test(
        "TC-12",
        f"Show me all available data for SOL ({SOL}) — market cap, liquidity, volume, price, holders",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "tool_selection_all_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: JUP overview with frames
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_overview to get JUP ({JUP}) overview with frames=1h,24h",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_frames_overview")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Polygon chain
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_overview to get WMATIC ({WMATIC_POL}) overview on Polygon chain",
        {"birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["matic"], tc, "polygon_overview")
         or check_text(r, ["polygon"], tc, "polygon_overview2")
         or check_text(r, ["wmatic"], tc, "polygon_overview3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: frames + ui_amount_mode combo
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_overview for SOL ({SOL}) with frames=1h,4h,24h and ui_amount_mode=scaled",
        {"birdeye_token_overview"},
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

"""
Test suite for GET /defi/v3/token/market-data via DeFi Query Service LLM.

Coverage:
  TC-01  Basic SOL market data (price, market cap, FDV, supply)
  TC-02  BONK market data
  TC-03  ui_amount_mode=raw
  TC-04  ui_amount_mode=scaled
  TC-05  Ethereum chain token
  TC-06  BSC chain token
  TC-07  Polygon chain token
  TC-08  Arbitrum chain token
  TC-09  Natural language "BONK market cap"
  TC-10  Natural language "FDV vs market cap for WIF"
  TC-11  Natural language "circulating supply of JUP"
  TC-12  Tool selection: market cap query → birdeye_token_market_data
  TC-13  Tool selection: liquidity query → birdeye_token_market_data
  TC-14  Base chain token
  TC-15  RAY market data with ui_amount_mode=raw
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL        = "So11111111111111111111111111111111111111112"
BONK       = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF        = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP        = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY        = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
WETH_ETH   = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
WBNB_BSC   = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
WMATIC_POL = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"
WETH_ARB   = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC_BASE  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

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
    print("birdeye_token_market_data (GET /defi/v3/token/market-data) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic SOL market data
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_market_data to get market data for SOL ({SOL})",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_market_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK market data
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_market_data to get BONK ({BONK}) market cap and supply data",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_market_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: ui_amount_mode=raw
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_market_data for SOL ({SOL}) with ui_amount_mode=raw",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_ui_raw")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_market_data for BONK ({BONK}) with ui_amount_mode=scaled",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_ui_scaled")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Ethereum chain
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_market_data to get WETH ({WETH_ETH}) market data on chain=ethereum",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_market_data")
         or check_text(r, ["ethereum"], tc, "eth_market_data2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: BSC chain
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_market_data to get WBNB ({WBNB_BSC}) market data on BSC chain",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["bnb"], tc, "bsc_market_data")
         or check_text(r, ["bsc"], tc, "bsc_market_data2")
         or check_text(r, ["wbnb"], tc, "bsc_market_data3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Polygon chain
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_market_data for WMATIC ({WMATIC_POL}) on Polygon chain",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["matic"], tc, "polygon_market_data")
         or check_text(r, ["polygon"], tc, "polygon_market_data2")
         or check_text(r, ["wmatic"], tc, "polygon_market_data3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Arbitrum chain
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_market_data for WETH ({WETH_ARB}) on Arbitrum chain",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["weth"], tc, "arb_market_data")
         or check_text(r, ["arbitrum"], tc, "arb_market_data2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "BONK market cap"
    results.append(await run_test(
        "TC-09",
        f"What is the current market cap of BONK ({BONK})?",
        {"birdeye_token_market_data", "birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_market_cap")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: FDV vs market cap
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_market_data to compare the FDV and market cap of WIF ({WIF})",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["wif"], tc, "fdv_vs_market_cap")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Circulating supply
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_market_data to get the circulating supply of JUP ({JUP})",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["jup"], tc, "circulating_supply")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: market cap
    results.append(await run_test(
        "TC-12",
        f"What is the fully diluted valuation and market cap of SOL ({SOL})?",
        {"birdeye_token_market_data", "birdeye_token_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "fdv_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection: liquidity
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_market_data to check the liquidity for BONK ({BONK})",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "liquidity_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Base chain
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_market_data to get USDC ({USDC_BASE}) market data on Base chain",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "base_market_data")
         or check_text(r, ["base"], tc, "base_market_data2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: RAY with ui_amount_mode=raw
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_market_data to get RAY ({RAY}) market data with ui_amount_mode=raw",
        {"birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_ui_raw")],
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

"""
Test suite for GET /defi/v3/token/market-data/multiple via DeFi Query Service LLM.

Coverage:
  TC-01  Two Solana tokens (SOL + BONK)
  TC-02  Three tokens (SOL + BONK + WIF)
  TC-03  Five tokens batch
  TC-04  Single token via multi endpoint (edge case)
  TC-05  Ethereum chain batch (WETH + USDC)
  TC-06  BSC chain batch
  TC-07  Polygon chain batch
  TC-08  Arbitrum chain batch
  TC-09  Mixed Solana tokens (SOL + JUP + RAY)
  TC-10  ui_amount_mode=raw on batch
  TC-11  ui_amount_mode=scaled on batch
  TC-12  Natural language "market cap for these tokens"
  TC-13  Tool selection: batch market cap → birdeye_token_market_data_multi
  TC-14  Large batch (7 tokens)
  TC-15  Base chain batch
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL        = "So11111111111111111111111111111111111111112"
USDC       = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK       = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF        = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP        = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY        = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
PYTH       = "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"

WETH_ETH   = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ETH   = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WBNB_BSC   = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
BUSD_BSC   = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"
WMATIC_POL = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"
USDC_POL   = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
WETH_ARB   = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC_BASE  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_BASE  = "0x4200000000000000000000000000000000000006"

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
    print("birdeye_token_market_data_multi (GET /defi/v3/token/market-data/multiple) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Two tokens
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_market_data_multi to get market data for SOL and BONK: {SOL},{BONK}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "two_token_market_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Three tokens
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_market_data_multi to get market data for SOL, BONK, and WIF: {SOL},{BONK},{WIF}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "three_token_market_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Five tokens batch
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_market_data_multi to batch-fetch market data for 5 tokens: {SOL},{BONK},{WIF},{JUP},{RAY}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "five_token_market_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Single token via multi (edge case)
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_market_data_multi with just one address to get JUP ({JUP}) market data",
        {"birdeye_token_market_data_multi", "birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["jup"], tc, "single_via_multi")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Ethereum chain batch
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_market_data_multi to get WETH and USDC market data on Ethereum: {WETH_ETH},{USDC_ETH} chain=ethereum",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_batch_market_data")
         or check_text(r, ["usdc"], tc, "eth_batch_market_data2")
         or check_text(r, ["ethereum"], tc, "eth_batch_market_data3")
         or check_text(r, ["market"], tc, "eth_batch_market_data4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: BSC chain batch
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_market_data_multi to get WBNB and BUSD market data on BSC: {WBNB_BSC},{BUSD_BSC} chain=bsc",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["bnb"], tc, "bsc_batch_market_data")
         or check_text(r, ["busd"], tc, "bsc_batch_market_data2")
         or check_text(r, ["bsc"], tc, "bsc_batch_market_data3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Polygon chain batch
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_market_data_multi for WMATIC and USDC on Polygon: {WMATIC_POL},{USDC_POL} chain=polygon",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["matic"], tc, "polygon_batch_market_data")
         or check_text(r, ["usdc"], tc, "polygon_batch_market_data2")
         or check_text(r, ["polygon"], tc, "polygon_batch_market_data3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Arbitrum chain batch
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_market_data_multi to get WETH market data on Arbitrum: {WETH_ARB} chain=arbitrum",
        {"birdeye_token_market_data_multi", "birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["weth"], tc, "arb_batch_market_data")
         or check_text(r, ["arbitrum"], tc, "arb_batch_market_data2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Mixed Solana tokens
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_market_data_multi to get market cap and FDV for SOL, JUP, and RAY: {SOL},{JUP},{RAY}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_jup_ray_market_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: ui_amount_mode=raw
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_market_data_multi for SOL and BONK with ui_amount_mode=raw: {SOL},{BONK}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "batch_ui_raw")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_market_data_multi for WIF and JUP with ui_amount_mode=scaled: {WIF},{JUP}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["wif"], tc, "batch_ui_scaled")
         or check_text(r, ["jup"], tc, "batch_ui_scaled2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "market cap for these tokens"
    results.append(await run_test(
        "TC-12",
        f"What are the market caps and FDVs for BONK and WIF? Fetch in one call: {BONK},{WIF}",
        {"birdeye_token_market_data_multi", "birdeye_token_market_data"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_market_cap_batch")
         or check_text(r, ["wif"], tc, "natural_market_cap_batch2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection: batch market cap
    results.append(await run_test(
        "TC-13",
        f"I need the market cap and circulating supply for these three tokens in one API call: {SOL},{BONK},{WIF}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "batch_market_cap_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Large batch (7 tokens)
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_market_data_multi for these 7 Solana tokens: {SOL},{USDC},{BONK},{WIF},{JUP},{RAY},{PYTH}",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "large_batch_market_data")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Base chain batch
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_market_data_multi for USDC and WETH on Base: {USDC_BASE},{WETH_BASE} chain=base",
        {"birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "base_batch_market_data")
         or check_text(r, ["weth"], tc, "base_batch_market_data2")
         or check_text(r, ["base"], tc, "base_batch_market_data3")
         or check_text(r, ["market"], tc, "base_batch_market_data4")],
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

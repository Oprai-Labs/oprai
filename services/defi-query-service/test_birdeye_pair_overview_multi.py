"""
Test suite for GET /defi/v3/pair/overview/multiple via DeFi Query Service LLM.

Coverage:
  TC-01  Two Solana pools (SOL/USDC + BONK/SOL)
  TC-02  Three Solana pools
  TC-03  Five pools batch
  TC-04  Single pool via multi endpoint (edge case)
  TC-05  ui_amount_mode=raw on batch
  TC-06  ui_amount_mode=scaled on batch
  TC-07  SOL/USDC + RAY/USDC + JUP/USDC batch
  TC-08  Ethereum Uniswap pools batch
  TC-09  Natural language "compare these pools"
  TC-10  Natural language "which pool has more TVL"
  TC-11  Natural language "batch pool overview"
  TC-12  Tool selection: batch pair overview → birdeye_pair_overview_multi
  TC-13  Tool selection: compare liquidity pools → birdeye_pair_overview_multi
  TC-14  WIF/SOL + BONK/SOL meme pools batch
  TC-15  ui_amount_mode=raw + batch combo
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

# Solana DEX pair addresses
SOL_USDC_RAY   = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWaS3CDpRZv6d"
BONK_SOL_RAY   = "8PhnCfgqpgFM7ZJvttGdBVMXHuU4Q23ACxCvvkYnA2Hm"
JUP_USDC       = "C1MgLojNLWBKADvu9BHdtgzz1oZX4dZ5zGdGcgvvW8Wz"
WIF_SOL        = "EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx"
RAY_USDC       = "6UmmUiYoBjSrhakAobJw8BvkmJtDVxaeBtbt7rxWo1mg"
USDC_USDT_ORCA = "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"

# Ethereum Uniswap v3 pools
WETH_USDC_ETH  = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
WETH_USDT_ETH  = "0x4e68ccd3e89f51c3074ca5072bbac773960dfa36"

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
    print("birdeye_pair_overview_multi (GET /defi/v3/pair/overview/multiple) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Two pools
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_pair_overview_multi to get an overview of the SOL/USDC and BONK/SOL pools: {SOL_USDC_RAY},{BONK_SOL_RAY}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "two_pools")
         or check_text(r, ["usdc"], tc, "two_pools2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Three pools
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_pair_overview_multi to get stats for three pools: {SOL_USDC_RAY},{BONK_SOL_RAY},{JUP_USDC}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "three_pools")
         or check_text(r, ["usdc"], tc, "three_pools2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Five pools
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_pair_overview_multi to get overview for 5 pools: {SOL_USDC_RAY},{BONK_SOL_RAY},{JUP_USDC},{WIF_SOL},{RAY_USDC}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "five_pools")
         or check_text(r, ["usdc"], tc, "five_pools2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Single pool via multi (edge case)
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_pair_overview_multi with just one pool to get SOL/USDC overview: {SOL_USDC_RAY}",
        {"birdeye_pair_overview_multi", "birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "single_via_multi")
         or check_text(r, ["usdc"], tc, "single_via_multi2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: ui_amount_mode=raw
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_pair_overview_multi for SOL/USDC and BONK/SOL pools with ui_amount_mode=raw: {SOL_USDC_RAY},{BONK_SOL_RAY}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "batch_pair_raw")
         or check_text(r, ["usdc"], tc, "batch_pair_raw2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_pair_overview_multi for JUP/USDC and WIF/SOL pools with ui_amount_mode=scaled: {JUP_USDC},{WIF_SOL}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["jup"], tc, "batch_pair_scaled")
         or check_text(r, ["wif"], tc, "batch_pair_scaled2")
         or check_text(r, ["usdc"], tc, "batch_pair_scaled3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: USDC pairs batch
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_pair_overview_multi to compare SOL/USDC, RAY/USDC, and JUP/USDC pools: {SOL_USDC_RAY},{RAY_USDC},{JUP_USDC}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "usdc_pairs_batch")
         or check_text(r, ["usdc"], tc, "usdc_pairs_batch2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Ethereum pools batch
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_pair_overview_multi to get WETH/USDC and WETH/USDT Uniswap pools on Ethereum: {WETH_USDC_ETH},{WETH_USDT_ETH} chain=ethereum",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_pools_batch")
         or check_text(r, ["usdc"], tc, "eth_pools_batch2")
         or check_text(r, ["ethereum"], tc, "eth_pools_batch3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "compare these pools"
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_pair_overview_multi to compare the SOL/USDC and BONK/SOL pools — TVL and volume: {SOL_USDC_RAY},{BONK_SOL_RAY}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_compare_pools")
         or check_text(r, ["usdc"], tc, "natural_compare_pools2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "which pool has more TVL"
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_pair_overview_multi to find which of these pools has more TVL: {SOL_USDC_RAY},{RAY_USDC},{JUP_USDC}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_more_tvl")
         or check_text(r, ["usdc"], tc, "natural_more_tvl2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "batch pool overview"
    results.append(await run_test(
        "TC-11",
        f"I need a full overview for these liquidity pools in one call — price, volume, TVL: {SOL_USDC_RAY},{BONK_SOL_RAY}",
        {"birdeye_pair_overview_multi", "birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_batch_overview")
         or check_text(r, ["usdc"], tc, "natural_batch_overview2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection
    results.append(await run_test(
        "TC-12",
        f"Get the DEX pair overview for these pool addresses in one batch call: {JUP_USDC},{WIF_SOL},{RAY_USDC}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["jup"], tc, "batch_pool_selection")
         or check_text(r, ["wif"], tc, "batch_pool_selection2")
         or check_text(r, ["usdc"], tc, "batch_pool_selection3")
         or check_text(r, ["pool"], tc, "batch_pool_selection4")
         or check_text(r, ["pair"], tc, "batch_pool_selection5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Compare liquidity pools
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_pair_overview_multi to compare liquidity and volume for the SOL/USDC and RAY/USDC Raydium pools: {SOL_USDC_RAY},{RAY_USDC}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "compare_liq_pools")
         or check_text(r, ["ray"], tc, "compare_liq_pools2")
         or check_text(r, ["usdc"], tc, "compare_liq_pools3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Meme pools batch
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_pair_overview_multi to get stats for WIF/SOL and BONK/SOL meme token pools: {WIF_SOL},{BONK_SOL_RAY}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["wif"], tc, "meme_pools_batch")
         or check_text(r, ["bonk"], tc, "meme_pools_batch2")
         or check_text(r, ["sol"], tc, "meme_pools_batch3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: ui_amount_mode=raw + batch
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_pair_overview_multi for SOL/USDC, JUP/USDC, and RAY/USDC pools with ui_amount_mode=raw: {SOL_USDC_RAY},{JUP_USDC},{RAY_USDC}",
        {"birdeye_pair_overview_multi"},
        [lambda r, tc: check_text(r, ["sol"], tc, "batch_raw_combo")
         or check_text(r, ["usdc"], tc, "batch_raw_combo2")],
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

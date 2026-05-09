"""
Test suite for GET /defi/v3/pair/overview/single via DeFi Query Service LLM.

Coverage:
  TC-01  SOL/USDC Raydium pool
  TC-02  BONK/SOL Raydium pool
  TC-03  JUP/USDC pool
  TC-04  WIF/SOL pool
  TC-05  ui_amount_mode=raw
  TC-06  ui_amount_mode=scaled
  TC-07  RAY/USDC pool
  TC-08  Ethereum Uniswap pool (WETH/USDC)
  TC-09  Natural language "get stats for this pool"
  TC-10  Natural language "what tokens are in this pool"
  TC-11  Natural language "TVL and volume for this pair"
  TC-12  Tool selection: pool overview → birdeye_pair_overview
  TC-13  Tool selection: pool address stats → birdeye_pair_overview
  TC-14  USDC/USDT pool
  TC-15  ui_amount_mode=raw + known pool combo
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

# Solana DEX pair addresses (Raydium/Orca pools)
SOL_USDC_RAY   = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWaS3CDpRZv6d"  # Raydium SOL/USDC
BONK_SOL_RAY   = "8PhnCfgqpgFM7ZJvttGdBVMXHuU4Q23ACxCvvkYnA2Hm"  # Raydium BONK/SOL
JUP_USDC       = "C1MgLojNLWBKADvu9BHdtgzz1oZX4dZ5zGdGcgvvW8Wz"  # JUP/USDC
WIF_SOL        = "EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx"   # WIF/SOL
RAY_USDC       = "6UmmUiYoBjSrhakAobJw8BvkmJtDVxaeBtbt7rxWo1mg"  # Raydium RAY/USDC
USDC_USDT_ORCA = "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"  # Orca USDC/USDT

# Ethereum Uniswap v3 WETH/USDC pool
WETH_USDC_ETH  = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"

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
    print("birdeye_pair_overview (GET /defi/v3/pair/overview/single) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL/USDC Raydium pool
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_pair_overview to get an overview of the SOL/USDC pool ({SOL_USDC_RAY})",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_usdc_pair")
         or check_text(r, ["usdc"], tc, "sol_usdc_pair2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK/SOL pool
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_pair_overview to get the overview of the BONK/SOL pool ({BONK_SOL_RAY})",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_sol_pair")
         or check_text(r, ["sol"], tc, "bonk_sol_pair2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: JUP/USDC pool
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_pair_overview to get stats for the JUP/USDC pool ({JUP_USDC})",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_usdc_pair")
         or check_text(r, ["usdc"], tc, "jup_usdc_pair2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: WIF/SOL pool
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_pair_overview to get overview of the WIF/SOL pool ({WIF_SOL})",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_sol_pair")
         or check_text(r, ["sol"], tc, "wif_sol_pair2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: ui_amount_mode=raw
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_pair_overview for SOL/USDC pool ({SOL_USDC_RAY}) with ui_amount_mode=raw",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "pair_ui_raw")
         or check_text(r, ["usdc"], tc, "pair_ui_raw2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_pair_overview for BONK/SOL pool ({BONK_SOL_RAY}) with ui_amount_mode=scaled",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "pair_ui_scaled")
         or check_text(r, ["sol"], tc, "pair_ui_scaled2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: RAY/USDC pool
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_pair_overview to get the RAY/USDC pool overview ({RAY_USDC})",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_usdc_pair")
         or check_text(r, ["usdc"], tc, "ray_usdc_pair2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Ethereum Uniswap pool
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_pair_overview to get the WETH/USDC Uniswap v3 pool ({WETH_USDC_ETH}) on chain=ethereum",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_pair_overview")
         or check_text(r, ["usdc"], tc, "eth_pair_overview2")
         or check_text(r, ["ethereum"], tc, "eth_pair_overview3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "get stats for this pool"
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_pair_overview to get the full stats for this liquidity pool: {SOL_USDC_RAY}",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["sol"], tc, "natural_pool_stats")
         or check_text(r, ["usdc"], tc, "natural_pool_stats2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "what tokens are in this pool"
    results.append(await run_test(
        "TC-10",
        f"What tokens are in this pool and what is its current price? Pool address: {BONK_SOL_RAY}",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_pool_tokens")
         or check_text(r, ["sol"], tc, "natural_pool_tokens2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "TVL and volume for this pair"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_pair_overview to get the TVL, volume, and fee tier for this pool: {RAY_USDC}",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["ray"], tc, "natural_tvl_volume")
         or check_text(r, ["usdc"], tc, "natural_tvl_volume2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: pool overview
    results.append(await run_test(
        "TC-12",
        f"Show me all available data for this DEX pool: {JUP_USDC}",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["jup"], tc, "tool_selection_pool")
         or check_text(r, ["usdc"], tc, "tool_selection_pool2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection: pool address stats
    results.append(await run_test(
        "TC-13",
        f"What is the 24h volume and liquidity for this liquidity pool address: {WIF_SOL}?",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["wif"], tc, "pool_address_stats")
         or check_text(r, ["sol"], tc, "pool_address_stats2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: USDC/USDT pool
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_pair_overview to get the overview of the USDC/USDT Orca pool ({USDC_USDT_ORCA})",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "usdc_usdt_pair")
         or check_text(r, ["usdt"], tc, "usdc_usdt_pair2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: ui_amount_mode=raw + pool
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_pair_overview for the JUP/USDC pool ({JUP_USDC}) with ui_amount_mode=raw",
        {"birdeye_pair_overview"},
        [lambda r, tc: check_text(r, ["jup"], tc, "pair_raw_combo")
         or check_text(r, ["usdc"], tc, "pair_raw_combo2")],
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

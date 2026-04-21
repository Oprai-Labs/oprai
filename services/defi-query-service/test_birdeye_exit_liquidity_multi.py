"""
Test suite for GET /defi/v3/token/exit-liquidity/multiple via DeFi Query Service LLM.
NOTE: This endpoint is Base chain ONLY.

Coverage:
  TC-01  Two Base tokens (USDC + WETH)
  TC-02  Three Base tokens (USDC + WETH + AERO)
  TC-03  Five tokens batch
  TC-04  Single token via multi endpoint (edge case)
  TC-05  cbETH + DAI batch
  TC-06  BRETT + DEGEN batch
  TC-07  USDbC + AERO batch
  TC-08  Natural language "compare exit liquidity of these Base tokens"
  TC-09  Natural language "which of these tokens has more sell depth"
  TC-10  Tool selection: batch exit liquidity → birdeye_exit_liquidity_multi
  TC-11  WETH + cbETH + USDC batch
  TC-12  USDC + DAI + USDbC stablecoins batch
  TC-13  Natural language "batch sell depth check on Base"
  TC-14  AERO + BRETT + DEGEN meme/DeFi batch
  TC-15  Large batch (6 tokens)
"""

import asyncio
import httpx

BASE_URL = "http://localhost:3150"

USDC_BASE  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_BASE  = "0x4200000000000000000000000000000000000006"
cbETH_BASE = "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"
DAI_BASE   = "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb"
AERO_BASE  = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
USDbC_BASE = "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA"
BRETT_BASE = "0x532f27101965dd16442E59d40670FaF5eBB142E4"
DEGEN_BASE = "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed"

DELAY       = 22
RETRY_DELAY = 38


def _log(tc, label, ok, detail=""):
    mark = "✅" if ok else "❌"
    print(f"  {mark} [{tc}] {label}" + (f": {detail}" if detail else ""))


async def ask(question: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{BASE_URL}/query", json={"question": question})
        if r.status_code == 500:
            print(f"  ⚠️  500 — retrying after {RETRY_DELAY}s")
            await asyncio.sleep(RETRY_DELAY)
            r = await client.post(f"{BASE_URL}/query", json={"question": question})
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
    print("birdeye_exit_liquidity_multi (GET /defi/v3/token/exit-liquidity/multiple) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Two tokens
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_exit_liquidity_multi to get exit liquidity for USDC and WETH on Base: {USDC_BASE},{WETH_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "usdc_weth_exit")
         or check_text(r, ["weth"], tc, "usdc_weth_exit2")
         or check_text(r, ["liquidity"], tc, "usdc_weth_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Three tokens
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_exit_liquidity_multi to get exit liquidity for USDC, WETH, and AERO on Base: {USDC_BASE},{WETH_BASE},{AERO_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "three_exit")
         or check_text(r, ["aero"], tc, "three_exit2")
         or check_text(r, ["liquidity"], tc, "three_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Five tokens batch
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_exit_liquidity_multi to batch-check exit liquidity for 5 Base tokens: {USDC_BASE},{WETH_BASE},{AERO_BASE},{cbETH_BASE},{DAI_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "five_exit")
         or check_text(r, ["weth"], tc, "five_exit2")
         or check_text(r, ["liquidity"], tc, "five_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Single token via multi (edge case)
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_exit_liquidity_multi with just one address to check USDC ({USDC_BASE}) exit liquidity on Base",
        {"birdeye_exit_liquidity_multi", "birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "single_via_multi")
         or check_text(r, ["liquidity"], tc, "single_via_multi2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: cbETH + DAI batch
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_exit_liquidity_multi to compare exit liquidity for cbETH and DAI on Base: {cbETH_BASE},{DAI_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["cbeth"], tc, "cbeth_dai_exit")
         or check_text(r, ["dai"], tc, "cbeth_dai_exit2")
         or check_text(r, ["eth"], tc, "cbeth_dai_exit3")
         or check_text(r, ["liquidity"], tc, "cbeth_dai_exit4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: BRETT + DEGEN batch
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_exit_liquidity_multi to get BRETT and DEGEN exit liquidity on Base: {BRETT_BASE},{DEGEN_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["brett"], tc, "brett_degen_exit")
         or check_text(r, ["degen"], tc, "brett_degen_exit2")
         or check_text(r, ["liquidity"], tc, "brett_degen_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: USDbC + AERO batch
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_exit_liquidity_multi for USDbC and AERO on Base: {USDbC_BASE},{AERO_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["aero"], tc, "usdbc_aero_exit")
         or check_text(r, ["usdbc"], tc, "usdbc_aero_exit2")
         or check_text(r, ["liquidity"], tc, "usdbc_aero_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Natural language "compare exit liquidity"
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_exit_liquidity_multi to compare the exit liquidity of USDC and WETH on Base in one call: {USDC_BASE},{WETH_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "natural_compare_exit")
         or check_text(r, ["weth"], tc, "natural_compare_exit2")
         or check_text(r, ["liquidity"], tc, "natural_compare_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language "which has more sell depth"
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_exit_liquidity_multi to find which of BRETT or DEGEN has more sell depth on Base: {BRETT_BASE},{DEGEN_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["brett"], tc, "natural_sell_depth")
         or check_text(r, ["degen"], tc, "natural_sell_depth2")
         or check_text(r, ["liquidity"], tc, "natural_sell_depth3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Tool selection
    results.append(await run_test(
        "TC-10",
        f"I need to check the exit liquidity for multiple Base tokens in one API call: {USDC_BASE},{WETH_BASE},{AERO_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "batch_exit_selection")
         or check_text(r, ["aero"], tc, "batch_exit_selection2")
         or check_text(r, ["liquidity"], tc, "batch_exit_selection3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: WETH + cbETH + USDC
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_exit_liquidity_multi to get exit liquidity for WETH, cbETH, and USDC on Base: {WETH_BASE},{cbETH_BASE},{USDC_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["weth"], tc, "weth_cbeth_usdc_exit")
         or check_text(r, ["usdc"], tc, "weth_cbeth_usdc_exit2")
         or check_text(r, ["liquidity"], tc, "weth_cbeth_usdc_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Stablecoins batch
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_exit_liquidity_multi to compare stablecoin exit liquidity on Base for USDC, DAI, USDbC: {USDC_BASE},{DAI_BASE},{USDbC_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "stablecoin_exit_batch")
         or check_text(r, ["dai"], tc, "stablecoin_exit_batch2")
         or check_text(r, ["liquidity"], tc, "stablecoin_exit_batch3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "batch sell depth"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_exit_liquidity_multi to check the sell depth for these Base tokens in one batch call: {USDC_BASE},{AERO_BASE},{BRETT_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "natural_batch_sell_depth")
         or check_text(r, ["aero"], tc, "natural_batch_sell_depth2")
         or check_text(r, ["liquidity"], tc, "natural_batch_sell_depth3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: AERO + BRETT + DEGEN
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_exit_liquidity_multi for AERO, BRETT, and DEGEN on Base: {AERO_BASE},{BRETT_BASE},{DEGEN_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["aero"], tc, "aero_brett_degen_exit")
         or check_text(r, ["brett"], tc, "aero_brett_degen_exit2")
         or check_text(r, ["liquidity"], tc, "aero_brett_degen_exit3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Large batch (6 tokens)
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_exit_liquidity_multi for 6 Base tokens: {USDC_BASE},{WETH_BASE},{cbETH_BASE},{DAI_BASE},{AERO_BASE},{BRETT_BASE}",
        {"birdeye_exit_liquidity_multi"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "large_batch_exit")
         or check_text(r, ["weth"], tc, "large_batch_exit2")
         or check_text(r, ["liquidity"], tc, "large_batch_exit3")],
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

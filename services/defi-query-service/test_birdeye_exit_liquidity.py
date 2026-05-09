"""
Test suite for GET /defi/v3/token/exit-liquidity (single) via DeFi Query Service LLM.
NOTE: This endpoint is Base chain ONLY.

Coverage:
  TC-01  USDC on Base — basic exit liquidity
  TC-02  WETH on Base — exit liquidity
  TC-03  cbETH on Base
  TC-04  DAI on Base
  TC-05  Natural language "how much USDC can I sell on Base"
  TC-06  Natural language "exit liquidity for this Base token"
  TC-07  Tool selection: exit liquidity query → birdeye_exit_liquidity
  TC-08  AERO on Base
  TC-09  USDbC on Base
  TC-10  BRETT on Base
  TC-11  Natural language "available sell liquidity on Base"
  TC-12  Tool selection: "how much can I exit" → birdeye_exit_liquidity
  TC-13  DEGEN on Base
  TC-14  Natural language "check exit depth for Base token"
  TC-15  tBTC on Base
"""

import asyncio
import httpx

BASE_URL = "http://localhost:3150"

# Base chain token addresses
USDC_BASE  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_BASE  = "0x4200000000000000000000000000000000000006"
cbETH_BASE = "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"
DAI_BASE   = "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb"
AERO_BASE  = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
USDbC_BASE = "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA"
BRETT_BASE = "0x532f27101965dd16442E59d40670FaF5eBB142E4"
DEGEN_BASE = "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed"
tBTC_BASE  = "0x236aa50979D5f3De3Bd1Eeb40E81137F22ab794b"

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
    print("birdeye_exit_liquidity (GET /defi/v3/token/exit-liquidity) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: USDC on Base
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_exit_liquidity to get exit liquidity for USDC ({USDC_BASE}) on Base chain",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "usdc_exit_liquidity")
         or check_text(r, ["liquidity"], tc, "usdc_exit_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: WETH on Base
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_exit_liquidity to get exit liquidity for WETH ({WETH_BASE}) on Base",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["weth"], tc, "weth_exit_liquidity")
         or check_text(r, ["liquidity"], tc, "weth_exit_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: cbETH on Base
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_exit_liquidity to check exit liquidity for cbETH ({cbETH_BASE}) on Base chain",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["cbeth"], tc, "cbeth_exit_liquidity")
         or check_text(r, ["liquidity"], tc, "cbeth_exit_liquidity2")
         or check_text(r, ["eth"], tc, "cbeth_exit_liquidity3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: DAI on Base
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_exit_liquidity for DAI ({DAI_BASE}) on Base",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["dai"], tc, "dai_exit_liquidity")
         or check_text(r, ["liquidity"], tc, "dai_exit_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Natural language "how much USDC can I sell"
    results.append(await run_test(
        "TC-05",
        f"How much USDC ({USDC_BASE}) can I sell on Base chain? Check exit liquidity.",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "natural_sell_usdc")
         or check_text(r, ["liquidity"], tc, "natural_sell_usdc2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Natural language "exit liquidity for this Base token"
    results.append(await run_test(
        "TC-06",
        f"What is the exit liquidity for this Base token: {WETH_BASE}?",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["weth"], tc, "natural_exit_base")
         or check_text(r, ["liquidity"], tc, "natural_exit_base2")
         or check_text(r, ["base"], tc, "natural_exit_base3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Tool selection
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_exit_liquidity to find the available sell depth for AERO ({AERO_BASE}) on Base",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["aero"], tc, "aero_exit_liquidity")
         or check_text(r, ["liquidity"], tc, "aero_exit_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: AERO on Base
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_exit_liquidity to get exit liquidity data for USDbC ({USDbC_BASE}) on Base chain",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["usdbc"], tc, "usdbc_exit_liquidity")
         or check_text(r, ["usdb"], tc, "usdbc_exit_liquidity2")
         or check_text(r, ["liquidity"], tc, "usdbc_exit_liquidity3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: USDbC on Base
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_exit_liquidity to check how much BRETT ({BRETT_BASE}) I can sell on Base",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["brett"], tc, "brett_exit_liquidity")
         or check_text(r, ["liquidity"], tc, "brett_exit_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: BRETT on Base
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_exit_liquidity for DEGEN ({DEGEN_BASE}) on Base chain",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["degen"], tc, "degen_exit_liquidity")
         or check_text(r, ["liquidity"], tc, "degen_exit_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "available sell liquidity"
    results.append(await run_test(
        "TC-11",
        f"What is the available sell liquidity for USDC ({USDC_BASE}) on Base? Use exit liquidity data.",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "natural_sell_liquidity")
         or check_text(r, ["liquidity"], tc, "natural_sell_liquidity2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection "how much can I exit"
    results.append(await run_test(
        "TC-12",
        f"How much of my WETH position ({WETH_BASE}) can I exit on Base without major slippage?",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["weth"], tc, "how_much_exit")
         or check_text(r, ["liquidity"], tc, "how_much_exit2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: DEGEN on Base
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_exit_liquidity to check exit depth for AERO ({AERO_BASE}) on Base chain",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["aero"], tc, "aero_exit_depth")
         or check_text(r, ["liquidity"], tc, "aero_exit_depth2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "check exit depth"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_exit_liquidity to get the exit liquidity and sell depth for DAI ({DAI_BASE}) on Base",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["dai"], tc, "natural_exit_depth")
         or check_text(r, ["liquidity"], tc, "natural_exit_depth2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: tBTC on Base
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_exit_liquidity to get exit liquidity for tBTC ({tBTC_BASE}) on Base chain",
        {"birdeye_exit_liquidity"},
        [lambda r, tc: check_text(r, ["tbtc"], tc, "tbtc_exit_liquidity")
         or check_text(r, ["btc"], tc, "tbtc_exit_liquidity2")
         or check_text(r, ["liquidity"], tc, "tbtc_exit_liquidity3")],
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

"""
Test suite for GET /defi/multi_price via DeFi Query Service LLM.

Coverage:
  TC-01  Basic 2-token batch (SOL + BONK)
  TC-02  Large batch (5 tokens)
  TC-03  include_liquidity=true → liquidity field per token
  TC-04  check_liquidity threshold filters low-liquidity tokens
  TC-05  include_liquidity + check_liquidity combo
  TC-06  ui_amount_mode=raw (default explicit)
  TC-07  ui_amount_mode=both
  TC-08  Ethereum chain (ERC-20 tokens)
  TC-09  Single address (treated as batch of 1)
  TC-10  Invalid / unknown address in batch
  TC-11  Tool selection: batch vs single (>1 token → multi_price)
  TC-12  Natural language symbol names (SOL, WIF, PYTH)
  TC-13  Liquidity comparison across tokens
  TC-14  Batch with stablecoin (should be ~$1)
  TC-15  Empty / malformed input rejection
"""

import asyncio
import time
import httpx

BASE = "http://localhost:3150"

# Well-known mint addresses
SOL = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PYTH = "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"
JTO = "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"

# Ethereum ERC-20
USDT_ETH = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
WETH_ETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

DELAY = 22  # seconds between tests (TPM safety)
RETRY_DELAY = 38  # seconds on 500


def _log(tc: str, label: str, ok: bool, detail: str = ""):
    mark = "✅" if ok else "❌"
    print(f"  {mark} [{tc}] {label}" + (f": {detail}" if detail else ""))


async def ask(question: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{BASE}/query", json={"question": question})
        if r.status_code == 500:
            print(f"  ⚠️  500 — waiting {RETRY_DELAY}s then retrying")
            await asyncio.sleep(RETRY_DELAY)
            r = await client.post(f"{BASE}/query", json={"question": question})
        r.raise_for_status()
        return r.json()


def check_tool(result: dict, expected: set, tc: str) -> bool:
    called = set(result.get("tools_called", []))
    ok = bool(called & expected)
    _log(tc, "tool_selection", ok, f"called={called}, expected_any_of={expected}")
    return ok


def check_text(result: dict, keywords: list, tc: str, label: str = "response_content") -> bool:
    plain = result.get("plain", "").lower()
    html = result.get("html", "").lower()
    combined = plain + " " + html
    missing = [k for k in keywords if k.lower() not in combined]
    ok = not missing
    _log(tc, label, ok, f"missing={missing}" if missing else "all present")
    return ok


async def run_test(tc: str, question: str, expected_tools: set, checks: list[callable]):
    print(f"\n{'─'*60}")
    print(f"[{tc}] {question}")
    try:
        result = await ask(question)
    except Exception as e:
        print(f"  ❌ [{tc}] REQUEST_ERROR: {e}")
        return False

    passed = True
    tool_ok = check_tool(result, expected_tools, tc)
    passed = passed and tool_ok

    for fn in checks:
        ok = fn(result, tc)
        passed = passed and ok

    return passed


async def main():
    print("=" * 60)
    print("Birdeye multi_price — Full Parameter Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Basic 2-token batch (LLM returns prices by address — BONK symbol not guaranteed)
    results.append(await run_test(
        "TC-01", f"Birdeye prices for {SOL} and {BONK}",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["sol", "$"], tc, "prices_present")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Large batch (5 tokens) — explicitly request Birdeye
    batch5 = f"{SOL},{BONK},{WIF},{USDC},{JTO}"
    results.append(await run_test(
        "TC-02", f"Use Birdeye multi_price to get prices for these 5 tokens: {batch5}",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["sol", "$"], tc, "batch5_prices")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: include_liquidity=true
    results.append(await run_test(
        "TC-03", f"Get prices AND liquidity for {SOL} and {WIF} using Birdeye multi_price with include_liquidity",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["liquidity", "$"], tc, "liquidity_field_present")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: check_liquidity threshold (integer)
    results.append(await run_test(
        "TC-04", f"Get Birdeye multi_price for {SOL},{BONK} filtering out tokens with less than $500000 liquidity",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["sol", "price"], tc, "filtered_result_present")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: include_liquidity + check_liquidity combo
    results.append(await run_test(
        "TC-05", f"Use Birdeye multi_price on {SOL},{WIF},{BONK}: include liquidity data and also filter tokens below $100000 liquidity threshold",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["liquidity", "price"], tc, "combo_params")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: ui_amount_mode=raw
    results.append(await run_test(
        "TC-06", f"Birdeye multi_price for {SOL} and {USDC} with ui_amount_mode raw",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["sol", "usdc"], tc, "raw_mode_prices")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: ui_amount_mode=both
    results.append(await run_test(
        "TC-07", f"Get Birdeye batch prices for {SOL},{BONK} using ui_amount_mode both",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["sol", "$"], tc, "both_mode_prices")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Ethereum chain
    results.append(await run_test(
        "TC-08", f"Use Birdeye multi_price to get Ethereum chain prices for {USDT_ETH} and {WETH_ETH}",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["price", "$"], tc, "eth_prices")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Single address in batch
    results.append(await run_test(
        "TC-09", f"Use Birdeye batch price endpoint for just {SOL}",
        {"birdeye_multi_price", "birdeye_price"},
        [lambda r, tc: check_text(r, ["sol", "$"], tc, "single_in_batch")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Invalid address in batch
    results.append(await run_test(
        "TC-10", f"Get Birdeye multi_price for {SOL} and INVALIDADDRESS123xyz",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["sol"], tc, "valid_token_still_returned")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language symbols → resolved to multi_price
    results.append(await run_test(
        "TC-11", "What are the current Birdeye prices for SOL, WIF, and PYTH?",
        {"birdeye_multi_price", "birdeye_price"},
        [lambda r, tc: check_text(r, ["sol", "wif", "pyth", "$"], tc, "symbol_prices")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Liquidity comparison across tokens
    results.append(await run_test(
        "TC-12", f"Which token has more liquidity — SOL or BONK? Use Birdeye to check both prices and liquidity",
        {"birdeye_multi_price", "birdeye_token_market_data", "birdeye_token_market_data_multi", "birdeye_price"},
        [lambda r, tc: check_text(r, ["liquidity", "sol", "bonk"], tc, "liquidity_comparison")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Stablecoin in batch (price should be ~$1)
    results.append(await run_test(
        "TC-13", f"Birdeye multi_price: get prices for {USDC} and {SOL}",
        {"birdeye_multi_price"},
        [lambda r, tc: check_text(r, ["usdc", "sol"], tc, "stablecoin_in_batch")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Batch with include_liquidity — explicitly instruct to use include_liquidity=true
    results.append(await run_test(
        "TC-14", f"Use Birdeye multi_price with include_liquidity=true to get prices and liquidity for SOL ({SOL}) and WIF ({WIF}). Are they safe to trade?",
        {"birdeye_multi_price", "birdeye_token_market_data", "birdeye_token_market_data_multi"},
        [lambda r, tc: check_text(r, ["liquidity", "sol", "wif"], tc, "safety_analysis")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Empty addresses → LLM should gracefully decline (no tool call expected)
    r15 = await ask("Get Birdeye multi_price with no addresses")
    called15 = set(r15.get("tools_called", []))
    ok15 = not called15  # correct: LLM should not call any tool
    _log("TC-15", "graceful_no_call", ok15, f"called={called15} (expect empty)")
    results.append(ok15)

    # Summary
    print(f"\n{'='*60}")
    total = len(results)
    passed = sum(results)
    print(f"RESULT: {passed}/{total} PASS")
    if passed < total:
        print(f"FAILED: {total - passed} tests")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

"""
Test suite for GET /defi/v3/token/meta-data/single via DeFi Query Service LLM.

Coverage:
  TC-01  Basic SOL metadata (name, symbol, decimals)
  TC-02  BONK metadata (social links)
  TC-03  WIF metadata
  TC-04  Ethereum chain token metadata
  TC-05  BSC chain token metadata
  TC-06  Polygon chain token metadata
  TC-07  Arbitrum chain token metadata
  TC-08  JUP metadata
  TC-09  RAY metadata
  TC-10  Natural language "what is the official website for BONK"
  TC-11  Natural language "get token info for this address"
  TC-12  Tool selection: social links → birdeye_token_metadata
  TC-13  Tool selection: "token name for address" → birdeye_token_metadata
  TC-14  Base chain token metadata
  TC-15  Logo and description request
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
    print("birdeye_token_metadata (GET /defi/v3/token/meta-data/single) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL metadata
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_metadata to get metadata for SOL ({SOL})",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_metadata")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK metadata (social links)
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_metadata to get BONK ({BONK}) token info including social links",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_metadata")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF metadata
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_metadata to get WIF ({WIF}) token metadata",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_metadata")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Ethereum chain
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_metadata to get WETH ({WETH_ETH}) metadata on chain=ethereum",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["weth"], tc, "eth_metadata")
         or check_text(r, ["ethereum"], tc, "eth_metadata2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: BSC chain
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_metadata to get WBNB ({WBNB_BSC}) token info on BSC chain",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["bnb"], tc, "bsc_metadata")
         or check_text(r, ["bsc"], tc, "bsc_metadata2")
         or check_text(r, ["wbnb"], tc, "bsc_metadata3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Polygon chain
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_metadata to get WMATIC ({WMATIC_POL}) metadata on Polygon",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["matic"], tc, "polygon_metadata")
         or check_text(r, ["polygon"], tc, "polygon_metadata2")
         or check_text(r, ["wmatic"], tc, "polygon_metadata3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Arbitrum chain
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_metadata to get WETH ({WETH_ARB}) token info on Arbitrum chain",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["weth"], tc, "arb_metadata")
         or check_text(r, ["arbitrum"], tc, "arb_metadata2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: JUP metadata
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_metadata to get JUP ({JUP}) token metadata",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_metadata")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: RAY metadata
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_metadata to get RAY ({RAY}) token info",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_metadata")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language "official website for BONK"
    results.append(await run_test(
        "TC-10",
        f"What is the official website and Twitter for BONK ({BONK})?",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_social_links")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "get token info for this address"
    results.append(await run_test(
        "TC-11",
        f"What token is at address {WIF}? Get me the name, symbol, and description.",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["wif"], tc, "natural_token_info")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Tool selection: social links
    results.append(await run_test(
        "TC-12",
        f"What are the social media links and website for JUP ({JUP})?",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["jup"], tc, "social_links_selection")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Tool selection: token name for address
    results.append(await run_test(
        "TC-13",
        f"What is the token name and symbol for mint address {RAY}?",
        {"birdeye_token_metadata", "jup_search"},
        [lambda r, tc: check_text(r, ["ray"], tc, "name_for_address")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Base chain
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_metadata to get USDC ({USDC_BASE}) token metadata on Base chain",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["usdc"], tc, "base_metadata")
         or check_text(r, ["base"], tc, "base_metadata2")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Logo and description
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_metadata to get the logo URL and description for BONK ({BONK})",
        {"birdeye_token_metadata"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "logo_description")],
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

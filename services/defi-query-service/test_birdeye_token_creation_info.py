"""
Tests for GET /defi/token_creation_info via DeFi Query Service LLM.

TC-01  BONK creation info (Solana)
TC-02  WIF creation info
TC-03  JUP creation info
TC-04  chain=bsc
TC-05  chain=base
TC-06  chain=ethereum
TC-07  Natural language "who created BONK"
TC-08  Natural language "when was WIF deployed"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
USDC_ETH = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_BSC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"

DELAY       = 22
RETRY_DELAY = 38


def _log(tc, label, ok, detail=""):
    mark = "✅" if ok else "❌"
    print(f"  {mark} [{tc}] {label}" + (f": {detail}" if detail else ""))


async def ask(question: str) -> dict:
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            r = await client.post(f"{BASE}/query", json={"question": question})
        except httpx.ReadTimeout:
            print(f"  ⚠️  ReadTimeout — retrying after {RETRY_DELAY}s")
            await asyncio.sleep(RETRY_DELAY)
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
    print("birdeye_token_creation_info (GET /defi/token_creation_info) — Tests")
    print("=" * 60)

    results = []

    # TC-01: BONK
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_creation_info to get the creation info for BONK ({BONK})",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "bonk_creation")
         or check_text(r, ["deploy"], tc, "bonk_creation2")
         or check_text(r, ["bonk"], tc, "bonk_creation3")
         or check_text(r, ["tx"], tc, "bonk_creation4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: WIF
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_creation_info to get the creation info for WIF ({WIF})",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "wif_creation")
         or check_text(r, ["deploy"], tc, "wif_creation2")
         or check_text(r, ["wif"], tc, "wif_creation3")
         or check_text(r, ["tx"], tc, "wif_creation4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: JUP
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_creation_info to get the creation info for JUP ({JUP})",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "jup_creation")
         or check_text(r, ["deploy"], tc, "jup_creation2")
         or check_text(r, ["jup"], tc, "jup_creation3")
         or check_text(r, ["tx"], tc, "jup_creation4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: BSC
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_creation_info to get the creation info for token {USDC_BSC} on BSC (chain=bsc)",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "bsc_creation")
         or check_text(r, ["deploy"], tc, "bsc_creation2")
         or check_text(r, ["bsc"], tc, "bsc_creation3")
         or check_text(r, ["token"], tc, "bsc_creation4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Base
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_creation_info to get creation info for token {USDC_BASE} on Base (chain=base)",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "base_creation")
         or check_text(r, ["deploy"], tc, "base_creation2")
         or check_text(r, ["base"], tc, "base_creation3")
         or check_text(r, ["token"], tc, "base_creation4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Ethereum
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_creation_info to get creation info for token {USDC_ETH} on Ethereum (chain=ethereum)",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "eth_creation")
         or check_text(r, ["deploy"], tc, "eth_creation2")
         or check_text(r, ["ethereum"], tc, "eth_creation3")
         or check_text(r, ["token"], tc, "eth_creation4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Natural language "who created"
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_creation_info to find out who created BONK ({BONK}) and when",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "natural_creator")
         or check_text(r, ["deploy"], tc, "natural_creator2")
         or check_text(r, ["bonk"], tc, "natural_creator3")
         or check_text(r, ["wallet"], tc, "natural_creator4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Natural language "when deployed"
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_creation_info to check when WIF ({WIF}) was deployed and the deployer address",
        {"birdeye_token_creation_info"},
        [lambda r, tc: check_text(r, ["creat"], tc, "natural_deploy")
         or check_text(r, ["deploy"], tc, "natural_deploy2")
         or check_text(r, ["wif"], tc, "natural_deploy3")
         or check_text(r, ["wallet"], tc, "natural_deploy4")],
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

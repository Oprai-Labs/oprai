"""
Tests for GET /defi/token_security via DeFi Query Service LLM.

TC-01  BONK security audit (Solana)
TC-02  WIF security audit
TC-03  JUP security audit
TC-04  SOL security audit
TC-05  RAY security audit
TC-06  chain=ethereum (USDC)
TC-07  chain=bsc
TC-08  chain=base
TC-09  Natural language "is BONK safe to buy"
TC-10  Natural language "rug check for WIF"
TC-11  Natural language "mint authority check for JUP"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
USDC_ETH  = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDC_BSC  = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

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
    print("birdeye_token_security (GET /defi/token_security) — Tests")
    print("=" * 60)

    results = []

    # TC-01: BONK
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_security to get the security audit for BONK ({BONK})",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "bonk_sec")
         or check_text(r, ["mint"], tc, "bonk_sec2")
         or check_text(r, ["bonk"], tc, "bonk_sec3")
         or check_text(r, ["holder"], tc, "bonk_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: WIF
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_security to get the security audit for WIF ({WIF})",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "wif_sec")
         or check_text(r, ["mint"], tc, "wif_sec2")
         or check_text(r, ["wif"], tc, "wif_sec3")
         or check_text(r, ["holder"], tc, "wif_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: JUP
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_security to get the security audit for JUP ({JUP})",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "jup_sec")
         or check_text(r, ["mint"], tc, "jup_sec2")
         or check_text(r, ["jup"], tc, "jup_sec3")
         or check_text(r, ["holder"], tc, "jup_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: SOL
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_security to run a security check on SOL ({SOL})",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "sol_sec")
         or check_text(r, ["mint"], tc, "sol_sec2")
         or check_text(r, ["sol"], tc, "sol_sec3")
         or check_text(r, ["holder"], tc, "sol_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: RAY
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_security to audit the security of RAY ({RAY})",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "ray_sec")
         or check_text(r, ["mint"], tc, "ray_sec2")
         or check_text(r, ["ray"], tc, "ray_sec3")
         or check_text(r, ["holder"], tc, "ray_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Ethereum
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_security to check security for token {USDC_ETH} on Ethereum (chain=ethereum)",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "eth_sec")
         or check_text(r, ["ethereum"], tc, "eth_sec2")
         or check_text(r, ["token"], tc, "eth_sec3")
         or check_text(r, ["mint"], tc, "eth_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: BSC
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_security to check security for token {USDC_BSC} on BSC (chain=bsc)",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "bsc_sec")
         or check_text(r, ["bsc"], tc, "bsc_sec2")
         or check_text(r, ["token"], tc, "bsc_sec3")
         or check_text(r, ["mint"], tc, "bsc_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Base
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_security to check security for token {USDC_BASE} on Base (chain=base)",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "base_sec")
         or check_text(r, ["base"], tc, "base_sec2")
         or check_text(r, ["token"], tc, "base_sec3")
         or check_text(r, ["mint"], tc, "base_sec4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: Natural language safe to buy
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_security to check if BONK ({BONK}) is safe to buy",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "natural_safe")
         or check_text(r, ["bonk"], tc, "natural_safe2")
         or check_text(r, ["mint"], tc, "natural_safe3")
         or check_text(r, ["holder"], tc, "natural_safe4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language rug check
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_security to do a rug check for WIF ({WIF})",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["security"], tc, "natural_rug")
         or check_text(r, ["wif"], tc, "natural_rug2")
         or check_text(r, ["mint"], tc, "natural_rug3")
         or check_text(r, ["holder"], tc, "natural_rug4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language mint authority
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_security to check the mint authority status for JUP ({JUP})",
        {"birdeye_token_security"},
        [lambda r, tc: check_text(r, ["mint"], tc, "natural_mint")
         or check_text(r, ["security"], tc, "natural_mint2")
         or check_text(r, ["jup"], tc, "natural_mint3")
         or check_text(r, ["authority"], tc, "natural_mint4")],
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

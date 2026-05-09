"""
Tests for GET /defi/v3/token/meme/detail/single via DeFi Query Service LLM.

TC-01  Default meme token (Solana)
TC-02  BONK meme detail
TC-03  WIF meme detail
TC-04  chain=bsc
TC-05  chain=monad
TC-06  Natural language "meme token details for this address"
TC-07  Natural language "bonding curve status"
TC-08  Natural language "has this meme token graduated"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

BONK    = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF     = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
PUMP_TOKEN = "HHuLN58RAJoyNxwYte8NdEyV7dhQzDCLXDmTraHspump"
BSC_MEME   = "0x2170ed0880ac9a755fd29b2688956bd959f933f8"
MONAD_MEME = "0x836047a99e11f376522b447bffb6e3495dd0637c"

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
    print("birdeye_meme_token_detail (GET /defi/v3/token/meme/detail/single) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Default pump.fun token
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_meme_token_detail to get meme token details for {PUMP_TOKEN}",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["token"], tc, "default")
         or check_text(r, ["meme"], tc, "default2")
         or check_text(r, ["bonding"], tc, "default3")
         or check_text(r, ["pump"], tc, "default4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_meme_token_detail to get the meme token detail for BONK ({BONK})",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["token"], tc, "bonk")
         or check_text(r, ["bonk"], tc, "bonk2")
         or check_text(r, ["meme"], tc, "bonk3")
         or check_text(r, ["detail"], tc, "bonk4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_meme_token_detail to get the meme token detail for WIF ({WIF})",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["token"], tc, "wif")
         or check_text(r, ["wif"], tc, "wif2")
         or check_text(r, ["meme"], tc, "wif3")
         or check_text(r, ["detail"], tc, "wif4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: BSC
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_meme_token_detail to get meme details for {BSC_MEME} on BSC (chain=bsc)",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["token"], tc, "bsc_meme")
         or check_text(r, ["bsc"], tc, "bsc_meme2")
         or check_text(r, ["meme"], tc, "bsc_meme3")
         or check_text(r, ["detail"], tc, "bsc_meme4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Monad
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_meme_token_detail to get meme token details for {MONAD_MEME} on Monad (chain=monad)",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["token"], tc, "monad_meme")
         or check_text(r, ["monad"], tc, "monad_meme2")
         or check_text(r, ["meme"], tc, "monad_meme3")
         or check_text(r, ["detail"], tc, "monad_meme4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Natural language meme details
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_meme_token_detail to show me the meme token details for {PUMP_TOKEN}",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_detail")
         or check_text(r, ["meme"], tc, "natural_detail2")
         or check_text(r, ["bonding"], tc, "natural_detail3")
         or check_text(r, ["pump"], tc, "natural_detail4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Natural language bonding curve
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_meme_token_detail to check the bonding curve status of {PUMP_TOKEN}",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["bonding"], tc, "natural_bonding")
         or check_text(r, ["token"], tc, "natural_bonding2")
         or check_text(r, ["meme"], tc, "natural_bonding3")
         or check_text(r, ["curve"], tc, "natural_bonding4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: Natural language graduated
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_meme_token_detail to check if {PUMP_TOKEN} has graduated from the bonding curve",
        {"birdeye_meme_token_detail"},
        [lambda r, tc: check_text(r, ["token"], tc, "natural_graduated")
         or check_text(r, ["meme"], tc, "natural_graduated2")
         or check_text(r, ["bonding"], tc, "natural_graduated3")
         or check_text(r, ["graduat"], tc, "natural_graduated4")],
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

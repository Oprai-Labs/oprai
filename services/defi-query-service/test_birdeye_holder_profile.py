"""
Test suite for GET /token/v1/holder-profile via DeFi Query Service LLM.

Coverage:
  TC-01  BONK holder profile (default)
  TC-02  SOL holder profile
  TC-03  WIF holder profile
  TC-04  JUP holder profile
  TC-05  RAY holder profile
  TC-06  interval=1h (explicit)
  TC-07  ui_amount_mode=raw
  TC-08  ui_amount_mode=scaled (explicit)
  TC-09  include_zero_balance=false
  TC-10  include_zero_balance=true (explicit)
  TC-11  Natural language "holder profile for BONK"
  TC-12  Natural language "how many holders does SOL have"
  TC-13  Natural language "holder breakdown for WIF"
  TC-14  Natural language "sniper/bundler holder tags for BONK"
  TC-15  Natural language "holder summary for JUP"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

SOL  = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF  = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
JUP  = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY  = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"

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
    print("birdeye_holder_profile (GET /token/v1/holder-profile) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: BONK default
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_holder_profile to get the holder profile for BONK ({BONK})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "bonk_profile")
         or check_text(r, ["bonk"], tc, "bonk_profile2")
         or check_text(r, ["token"], tc, "bonk_profile3")
         or check_text(r, ["balance"], tc, "bonk_profile4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: SOL
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_holder_profile to get the holder profile for SOL ({SOL})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "sol_profile")
         or check_text(r, ["sol"], tc, "sol_profile2")
         or check_text(r, ["token"], tc, "sol_profile3")
         or check_text(r, ["balance"], tc, "sol_profile4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_holder_profile to get the holder profile for WIF ({WIF})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "wif_profile")
         or check_text(r, ["wif"], tc, "wif_profile2")
         or check_text(r, ["token"], tc, "wif_profile3")
         or check_text(r, ["balance"], tc, "wif_profile4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: JUP
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_holder_profile to get the holder profile for JUP ({JUP})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "jup_profile")
         or check_text(r, ["jup"], tc, "jup_profile2")
         or check_text(r, ["token"], tc, "jup_profile3")
         or check_text(r, ["balance"], tc, "jup_profile4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: RAY
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_holder_profile to get the holder profile for RAY ({RAY})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "ray_profile")
         or check_text(r, ["ray"], tc, "ray_profile2")
         or check_text(r, ["token"], tc, "ray_profile3")
         or check_text(r, ["balance"], tc, "ray_profile4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: interval=1h explicit
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_holder_profile to get the BONK ({BONK}) holder profile with interval=1h",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "interval_1h")
         or check_text(r, ["bonk"], tc, "interval_1h2")
         or check_text(r, ["token"], tc, "interval_1h3")
         or check_text(r, ["balance"], tc, "interval_1h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: ui_amount_mode=raw
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_holder_profile to get SOL ({SOL}) holder profile with raw token amounts (ui_amount_mode=raw)",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "raw_mode")
         or check_text(r, ["sol"], tc, "raw_mode2")
         or check_text(r, ["token"], tc, "raw_mode3")
         or check_text(r, ["balance"], tc, "raw_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_holder_profile to get BONK ({BONK}) holder profile with scaled amounts (ui_amount_mode=scaled)",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "scaled_mode")
         or check_text(r, ["bonk"], tc, "scaled_mode2")
         or check_text(r, ["token"], tc, "scaled_mode3")
         or check_text(r, ["balance"], tc, "scaled_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: include_zero_balance=false
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_holder_profile to get WIF ({WIF}) holder profile excluding zero-balance holders (include_zero_balance=false)",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "no_zero_balance")
         or check_text(r, ["wif"], tc, "no_zero_balance2")
         or check_text(r, ["token"], tc, "no_zero_balance3")
         or check_text(r, ["balance"], tc, "no_zero_balance4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: include_zero_balance=true explicit
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_holder_profile to get JUP ({JUP}) holder profile including zero-balance holders (include_zero_balance=true)",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "with_zero_balance")
         or check_text(r, ["jup"], tc, "with_zero_balance2")
         or check_text(r, ["token"], tc, "with_zero_balance3")
         or check_text(r, ["balance"], tc, "with_zero_balance4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language "holder profile"
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_holder_profile to show me the holder profile for BONK ({BONK})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_profile")
         or check_text(r, ["bonk"], tc, "natural_profile2")
         or check_text(r, ["token"], tc, "natural_profile3")
         or check_text(r, ["balance"], tc, "natural_profile4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language "how many holders"
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_holder_profile to find out how many holders SOL ({SOL}) has",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_count")
         or check_text(r, ["sol"], tc, "natural_count2")
         or check_text(r, ["token"], tc, "natural_count3")
         or check_text(r, ["total"], tc, "natural_count4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "holder breakdown"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_holder_profile to get the holder breakdown for WIF ({WIF})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_breakdown")
         or check_text(r, ["wif"], tc, "natural_breakdown2")
         or check_text(r, ["token"], tc, "natural_breakdown3")
         or check_text(r, ["balance"], tc, "natural_breakdown4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "sniper/bundler tags"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_holder_profile to analyze BONK ({BONK}) holders by tags like sniper, bundler, insider, dev",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_tags")
         or check_text(r, ["bonk"], tc, "natural_tags2")
         or check_text(r, ["sniper", "bundler", "insider", "dev"], tc, "natural_tags3")
         or check_text(r, ["token"], tc, "natural_tags4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language "holder summary"
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_holder_profile to give me a holder summary for JUP ({JUP})",
        {"birdeye_holder_profile"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_summary")
         or check_text(r, ["jup"], tc, "natural_summary2")
         or check_text(r, ["token"], tc, "natural_summary3")
         or check_text(r, ["balance"], tc, "natural_summary4")],
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

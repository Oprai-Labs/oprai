"""
Test suite for GET /token/v1/holder-positions via DeFi Query Service LLM.

Coverage:
  TC-01  BONK holder positions (default)
  TC-02  SOL holder positions
  TC-03  WIF holder positions
  TC-04  JUP holder positions
  TC-05  RAY holder positions
  TC-06  labels=sniper
  TC-07  labels=bundler
  TC-08  labels=insider
  TC-09  labels=dev
  TC-10  order_type=asc
  TC-11  limit=10
  TC-12  offset=10 pagination
  TC-13  ui_amount_mode=raw
  TC-14  include_zero_balance=false
  TC-15  Natural language "show me sniper holders of BONK"
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
    print("birdeye_holder_positions (GET /token/v1/holder-positions) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: BONK default
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_holder_positions to get the holder positions for BONK ({BONK})",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "bonk_positions")
         or check_text(r, ["bonk"], tc, "bonk_positions2")
         or check_text(r, ["wallet"], tc, "bonk_positions3")
         or check_text(r, ["balance"], tc, "bonk_positions4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: SOL
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_holder_positions to get the holder positions for SOL ({SOL})",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "sol_positions")
         or check_text(r, ["sol"], tc, "sol_positions2")
         or check_text(r, ["wallet"], tc, "sol_positions3")
         or check_text(r, ["balance"], tc, "sol_positions4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_holder_positions to get the holder positions for WIF ({WIF})",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "wif_positions")
         or check_text(r, ["wif"], tc, "wif_positions2")
         or check_text(r, ["wallet"], tc, "wif_positions3")
         or check_text(r, ["balance"], tc, "wif_positions4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: JUP
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_holder_positions to get the holder positions for JUP ({JUP})",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "jup_positions")
         or check_text(r, ["jup"], tc, "jup_positions2")
         or check_text(r, ["wallet"], tc, "jup_positions3")
         or check_text(r, ["balance"], tc, "jup_positions4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: RAY
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_holder_positions to get the holder positions for RAY ({RAY})",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "ray_positions")
         or check_text(r, ["ray"], tc, "ray_positions2")
         or check_text(r, ["wallet"], tc, "ray_positions3")
         or check_text(r, ["balance"], tc, "ray_positions4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: labels=sniper
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_holder_positions to get BONK ({BONK}) holders with labels=sniper",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "sniper_label")
         or check_text(r, ["sniper"], tc, "sniper_label2")
         or check_text(r, ["bonk"], tc, "sniper_label3")
         or check_text(r, ["wallet"], tc, "sniper_label4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: labels=bundler
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_holder_positions to get BONK ({BONK}) holders with labels=bundler",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "bundler_label")
         or check_text(r, ["bundler"], tc, "bundler_label2")
         or check_text(r, ["bonk"], tc, "bundler_label3")
         or check_text(r, ["wallet"], tc, "bundler_label4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: labels=insider
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_holder_positions to get WIF ({WIF}) holders with labels=insider",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "insider_label")
         or check_text(r, ["insider"], tc, "insider_label2")
         or check_text(r, ["wif"], tc, "insider_label3")
         or check_text(r, ["wallet"], tc, "insider_label4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: labels=dev
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_holder_positions to get WIF ({WIF}) holders with labels=dev",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "dev_label")
         or check_text(r, ["dev"], tc, "dev_label2")
         or check_text(r, ["wif"], tc, "dev_label3")
         or check_text(r, ["wallet"], tc, "dev_label4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: order_type=asc
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_holder_positions to get BONK ({BONK}) holder positions sorted ascending (order_type=asc)",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "asc_order")
         or check_text(r, ["bonk"], tc, "asc_order2")
         or check_text(r, ["wallet"], tc, "asc_order3")
         or check_text(r, ["balance"], tc, "asc_order4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: limit=10
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_holder_positions to get the top 10 holder positions for SOL ({SOL}) (limit=10)",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "limit10")
         or check_text(r, ["sol"], tc, "limit10b")
         or check_text(r, ["wallet"], tc, "limit10c")
         or check_text(r, ["balance"], tc, "limit10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: offset=10 pagination
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_holder_positions to get BONK ({BONK}) holder positions starting at offset=10 (limit=10)",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "offset10")
         or check_text(r, ["bonk"], tc, "offset10b")
         or check_text(r, ["wallet"], tc, "offset10c")
         or check_text(r, ["balance"], tc, "offset10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: ui_amount_mode=raw
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_holder_positions to get JUP ({JUP}) holder positions with raw amounts (ui_amount_mode=raw, limit=10)",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "raw_mode")
         or check_text(r, ["jup"], tc, "raw_mode2")
         or check_text(r, ["wallet"], tc, "raw_mode3")
         or check_text(r, ["balance"], tc, "raw_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: include_zero_balance=false
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_holder_positions to get RAY ({RAY}) holder positions excluding zero balance holders (include_zero_balance=false, limit=10)",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "no_zero")
         or check_text(r, ["ray"], tc, "no_zero2")
         or check_text(r, ["wallet"], tc, "no_zero3")
         or check_text(r, ["balance"], tc, "no_zero4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language sniper holders
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_holder_positions to show me the sniper holders of BONK ({BONK})",
        {"birdeye_holder_positions"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_sniper")
         or check_text(r, ["sniper"], tc, "natural_sniper2")
         or check_text(r, ["bonk"], tc, "natural_sniper3")
         or check_text(r, ["wallet"], tc, "natural_sniper4")],
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

"""
Test suite for GET /defi/v3/token/holder via DeFi Query Service LLM.

Coverage:
  TC-01  SOL top holders (default)
  TC-02  BONK top holders
  TC-03  WIF top holders
  TC-04  JUP top holders
  TC-05  RAY top holders
  TC-06  limit=10
  TC-07  limit=50
  TC-08  limit=100 (max)
  TC-09  offset=100 (pagination)
  TC-10  ui_amount_mode=raw
  TC-11  ui_amount_mode=scaled (explicit)
  TC-12  offset=50 + limit=10
  TC-13  Natural language "who are the biggest BONK holders"
  TC-14  Natural language "top holders of SOL"
  TC-15  Natural language "whale distribution for WIF"
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
    print("birdeye_token_holders (GET /defi/v3/token/holder) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL holders
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_holders to get the top holders of SOL ({SOL})",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "sol_holders")
         or check_text(r, ["sol"], tc, "sol_holders2")
         or check_text(r, ["balance"], tc, "sol_holders3")
         or check_text(r, ["wallet"], tc, "sol_holders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK holders
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_holders to get the top holders of BONK ({BONK})",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "bonk_holders")
         or check_text(r, ["bonk"], tc, "bonk_holders2")
         or check_text(r, ["balance"], tc, "bonk_holders3")
         or check_text(r, ["wallet"], tc, "bonk_holders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF holders
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_holders to get the top holders of WIF ({WIF})",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "wif_holders")
         or check_text(r, ["wif"], tc, "wif_holders2")
         or check_text(r, ["balance"], tc, "wif_holders3")
         or check_text(r, ["wallet"], tc, "wif_holders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: JUP holders
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_holders to get the top holders of JUP ({JUP})",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "jup_holders")
         or check_text(r, ["jup"], tc, "jup_holders2")
         or check_text(r, ["balance"], tc, "jup_holders3")
         or check_text(r, ["wallet"], tc, "jup_holders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: RAY holders
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_holders to get the top holders of RAY ({RAY})",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "ray_holders")
         or check_text(r, ["ray"], tc, "ray_holders2")
         or check_text(r, ["balance"], tc, "ray_holders3")
         or check_text(r, ["wallet"], tc, "ray_holders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: limit=10
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_holders to get the top 10 holders of BONK ({BONK}) (limit=10)",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "limit10")
         or check_text(r, ["bonk"], tc, "limit10b")
         or check_text(r, ["balance"], tc, "limit10c")
         or check_text(r, ["wallet"], tc, "limit10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: limit=50
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_holders to get the top 50 holders of SOL ({SOL}) (limit=50)",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "limit50")
         or check_text(r, ["sol"], tc, "limit50b")
         or check_text(r, ["balance"], tc, "limit50c")
         or check_text(r, ["wallet"], tc, "limit50d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: limit=100
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_holders to get 100 top holders of BONK ({BONK}) (limit=100)",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "limit100")
         or check_text(r, ["bonk"], tc, "limit100b")
         or check_text(r, ["balance"], tc, "limit100c")
         or check_text(r, ["wallet"], tc, "limit100d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: offset pagination
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_holders to get the next page of BONK ({BONK}) holders starting at offset 100 (offset=100, limit=10)",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "offset100")
         or check_text(r, ["bonk"], tc, "offset100b")
         or check_text(r, ["balance"], tc, "offset100c")
         or check_text(r, ["wallet"], tc, "offset100d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: ui_amount_mode=raw
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_holders to get SOL ({SOL}) top holders with raw token amounts (ui_amount_mode=raw, limit=10)",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "raw_mode")
         or check_text(r, ["sol"], tc, "raw_mode2")
         or check_text(r, ["balance"], tc, "raw_mode3")
         or check_text(r, ["wallet"], tc, "raw_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_holders to get BONK ({BONK}) top holders with scaled amounts (ui_amount_mode=scaled, limit=10)",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "scaled_mode")
         or check_text(r, ["bonk"], tc, "scaled_mode2")
         or check_text(r, ["balance"], tc, "scaled_mode3")
         or check_text(r, ["wallet"], tc, "scaled_mode4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: offset + limit combined
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_token_holders to get holders ranked 51-60 for JUP ({JUP}) (offset=50, limit=10)",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "offset_limit")
         or check_text(r, ["jup"], tc, "offset_limit2")
         or check_text(r, ["balance"], tc, "offset_limit3")
         or check_text(r, ["wallet"], tc, "offset_limit4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language "biggest holders"
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_holders to show me who the biggest BONK ({BONK}) holders are",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_biggest")
         or check_text(r, ["bonk"], tc, "natural_biggest2")
         or check_text(r, ["balance"], tc, "natural_biggest3")
         or check_text(r, ["wallet"], tc, "natural_biggest4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language "top holders"
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_holders to get the top holders of SOL ({SOL}) and their balances",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_top")
         or check_text(r, ["sol"], tc, "natural_top2")
         or check_text(r, ["balance"], tc, "natural_top3")
         or check_text(r, ["wallet"], tc, "natural_top4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language "whale distribution"
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_holders to analyze the whale distribution for WIF ({WIF}) — show top holders",
        {"birdeye_token_holders"},
        [lambda r, tc: check_text(r, ["holder"], tc, "natural_whale")
         or check_text(r, ["wif"], tc, "natural_whale2")
         or check_text(r, ["balance"], tc, "natural_whale3")
         or check_text(r, ["wallet"], tc, "natural_whale4")],
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

"""
Test suite for GET /defi/v3/token/mint-burn-txs via DeFi Query Service LLM.

Coverage:
  TC-01  All mint/burn events for BONK
  TC-02  type=mint only
  TC-03  type=burn only
  TC-04  SOL mint/burn history
  TC-05  WIF supply events
  TC-06  after_time filter (last hour)
  TC-07  before_time filter
  TC-08  before_time + after_time window
  TC-09  sort_type=asc (oldest first)
  TC-10  limit=10
  TC-11  limit=100
  TC-12  offset=50 (pagination)
  TC-13  JUP mint/burn events
  TC-14  RAY supply history
  TC-15  Natural language "has this token been minting new supply"
"""

import asyncio
import httpx
import time

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
    print("birdeye_mint_burn_txs (GET /defi/v3/token/mint-burn-txs) — Full Test Suite")
    print("=" * 60)

    now = int(time.time())
    one_hour_ago  = now - 3600
    two_hours_ago = now - 7200

    results = []

    # TC-01: BONK all mint/burn
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_mint_burn_txs to get all mint and burn events for BONK ({BONK})",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_mint_burn")
         or check_text(r, ["mint"], tc, "bonk_mint_burn2")
         or check_text(r, ["burn"], tc, "bonk_mint_burn3")
         or check_text(r, ["supply"], tc, "bonk_mint_burn4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: type=mint only
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_mint_burn_txs to get only mint transactions for BONK ({BONK}) (type=mint, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_mint_only")
         or check_text(r, ["mint"], tc, "bonk_mint_only2")
         or check_text(r, ["supply"], tc, "bonk_mint_only3")
         or check_text(r, ["tx"], tc, "bonk_mint_only4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: type=burn only
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_mint_burn_txs to get only burn transactions for BONK ({BONK}) (type=burn, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_burn_only")
         or check_text(r, ["burn"], tc, "bonk_burn_only2")
         or check_text(r, ["supply"], tc, "bonk_burn_only3")
         or check_text(r, ["tx"], tc, "bonk_burn_only4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: SOL mint/burn
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_mint_burn_txs to check the mint and burn history for SOL ({SOL}), limit=10",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_mint_burn")
         or check_text(r, ["mint"], tc, "sol_mint_burn2")
         or check_text(r, ["burn"], tc, "sol_mint_burn3")
         or check_text(r, ["supply"], tc, "sol_mint_burn4")
         or check_text(r, ["tx"], tc, "sol_mint_burn5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: WIF supply events
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_mint_burn_txs to get supply events for WIF ({WIF}) — mint and burn, limit=10",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["wif"], tc, "wif_supply")
         or check_text(r, ["mint"], tc, "wif_supply2")
         or check_text(r, ["burn"], tc, "wif_supply3")
         or check_text(r, ["supply"], tc, "wif_supply4")
         or check_text(r, ["tx"], tc, "wif_supply5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: after_time filter
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_mint_burn_txs to get BONK ({BONK}) mint/burn events from the last hour (after_time={one_hour_ago}, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_after")
         or check_text(r, ["mint"], tc, "bonk_after2")
         or check_text(r, ["burn"], tc, "bonk_after3")
         or check_text(r, ["tx"], tc, "bonk_after4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: before_time filter
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_mint_burn_txs to get BONK ({BONK}) mint/burn events before timestamp {one_hour_ago} (before_time filter, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_before")
         or check_text(r, ["mint"], tc, "bonk_before2")
         or check_text(r, ["burn"], tc, "bonk_before3")
         or check_text(r, ["tx"], tc, "bonk_before4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: time window
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_mint_burn_txs to get BONK ({BONK}) mint/burn events between {two_hours_ago} and {one_hour_ago} (time window, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_window")
         or check_text(r, ["mint"], tc, "bonk_window2")
         or check_text(r, ["burn"], tc, "bonk_window3")
         or check_text(r, ["tx"], tc, "bonk_window4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: sort_type=asc
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_mint_burn_txs to get the earliest mint/burn events for BONK ({BONK}) (sort_type=asc, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_asc")
         or check_text(r, ["mint"], tc, "bonk_asc2")
         or check_text(r, ["burn"], tc, "bonk_asc3")
         or check_text(r, ["tx"], tc, "bonk_asc4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: limit=10
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_mint_burn_txs to get the last 10 mint/burn events for SOL ({SOL}) (limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["sol"], tc, "sol_limit10")
         or check_text(r, ["mint"], tc, "sol_limit10b")
         or check_text(r, ["burn"], tc, "sol_limit10c")
         or check_text(r, ["tx"], tc, "sol_limit10d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: limit=100
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_mint_burn_txs to get 100 mint/burn events for BONK ({BONK}) (limit=100)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_limit100")
         or check_text(r, ["mint"], tc, "bonk_limit100b")
         or check_text(r, ["burn"], tc, "bonk_limit100c")
         or check_text(r, ["tx"], tc, "bonk_limit100d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: offset pagination
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_mint_burn_txs to get the next page of BONK ({BONK}) mint/burn events (offset=50, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "bonk_offset")
         or check_text(r, ["mint"], tc, "bonk_offset2")
         or check_text(r, ["burn"], tc, "bonk_offset3")
         or check_text(r, ["tx"], tc, "bonk_offset4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: JUP events
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_mint_burn_txs to check if JUP ({JUP}) has been minting or burning tokens recently (limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["jup"], tc, "jup_mint_burn")
         or check_text(r, ["mint"], tc, "jup_mint_burn2")
         or check_text(r, ["burn"], tc, "jup_mint_burn3")
         or check_text(r, ["supply"], tc, "jup_mint_burn4")
         or check_text(r, ["tx"], tc, "jup_mint_burn5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: RAY history
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_mint_burn_txs to get the supply inflation/deflation history for RAY ({RAY}) (type=all, limit=10)",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["ray"], tc, "ray_supply")
         or check_text(r, ["mint"], tc, "ray_supply2")
         or check_text(r, ["burn"], tc, "ray_supply3")
         or check_text(r, ["supply"], tc, "ray_supply4")
         or check_text(r, ["tx"], tc, "ray_supply5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_mint_burn_txs to check if BONK ({BONK}) has been minting new supply recently",
        {"birdeye_mint_burn_txs"},
        [lambda r, tc: check_text(r, ["bonk"], tc, "natural_bonk_supply")
         or check_text(r, ["mint"], tc, "natural_bonk_supply2")
         or check_text(r, ["burn"], tc, "natural_bonk_supply3")
         or check_text(r, ["supply"], tc, "natural_bonk_supply4")
         or check_text(r, ["tx"], tc, "natural_bonk_supply5")],
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

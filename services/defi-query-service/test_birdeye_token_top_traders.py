"""
Test suite for GET /defi/v2/tokens/top_traders via DeFi Query Service LLM.

Coverage:
  TC-01  SOL top traders by volume (default)
  TC-02  BONK top traders 24h
  TC-03  WIF top traders 7d
  TC-04  sort_by=trade (by trade count)
  TC-05  sort_by=realized_pnl
  TC-06  sort_by=unrealized_pnl
  TC-07  sort_by=total_pnl
  TC-08  time_frame=1h
  TC-09  time_frame=7d
  TC-10  time_frame=30d
  TC-11  limit=5
  TC-12  offset=10 (pagination)
  TC-13  sort_type=asc (lowest volume first)
  TC-14  Natural language "who are the top traders on BONK"
  TC-15  JUP top traders by realized PnL 7d
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
    print("birdeye_token_top_traders (GET /defi/v2/tokens/top_traders) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: SOL top traders by volume
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_token_top_traders to get the top traders for SOL ({SOL}) by volume in the last 24h",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "sol_top_traders")
         or check_text(r, ["volume"], tc, "sol_top_traders2")
         or check_text(r, ["wallet"], tc, "sol_top_traders3")
         or check_text(r, ["sol"], tc, "sol_top_traders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: BONK top traders 24h
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_token_top_traders to get the top traders for BONK ({BONK}) in the last 24 hours (time_frame=24h, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "bonk_top_traders")
         or check_text(r, ["volume"], tc, "bonk_top_traders2")
         or check_text(r, ["wallet"], tc, "bonk_top_traders3")
         or check_text(r, ["bonk"], tc, "bonk_top_traders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: WIF top traders 7d
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_token_top_traders to get the top traders for WIF ({WIF}) over the last 7 days (time_frame=7d, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "wif_top_traders")
         or check_text(r, ["volume"], tc, "wif_top_traders2")
         or check_text(r, ["wallet"], tc, "wif_top_traders3")
         or check_text(r, ["wif"], tc, "wif_top_traders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: sort_by=trade
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_token_top_traders to get the most active traders on BONK ({BONK}) by trade count (sort_by=trade, time_frame=24h, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "bonk_by_trade")
         or check_text(r, ["trade"], tc, "bonk_by_trade2")
         or check_text(r, ["wallet"], tc, "bonk_by_trade3")
         or check_text(r, ["bonk"], tc, "bonk_by_trade4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: sort_by=realized_pnl
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_token_top_traders to get the most profitable traders on SOL ({SOL}) by realized PnL (sort_by=realized_pnl, time_frame=24h, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "sol_realized_pnl")
         or check_text(r, ["pnl"], tc, "sol_realized_pnl2")
         or check_text(r, ["profit"], tc, "sol_realized_pnl3")
         or check_text(r, ["sol"], tc, "sol_realized_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: sort_by=unrealized_pnl
    results.append(await run_test(
        "TC-06",
        f"Use birdeye_token_top_traders to get BONK ({BONK}) traders with the highest unrealized PnL (sort_by=unrealized_pnl, time_frame=7d, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "bonk_unrealized")
         or check_text(r, ["pnl"], tc, "bonk_unrealized2")
         or check_text(r, ["profit"], tc, "bonk_unrealized3")
         or check_text(r, ["bonk"], tc, "bonk_unrealized4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_by=total_pnl
    results.append(await run_test(
        "TC-07",
        f"Use birdeye_token_top_traders to get WIF ({WIF}) traders ranked by total PnL (sort_by=total_pnl, time_frame=7d, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "wif_total_pnl")
         or check_text(r, ["pnl"], tc, "wif_total_pnl2")
         or check_text(r, ["profit"], tc, "wif_total_pnl3")
         or check_text(r, ["wif"], tc, "wif_total_pnl4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: time_frame=1h
    results.append(await run_test(
        "TC-08",
        f"Use birdeye_token_top_traders to get the top SOL ({SOL}) traders in the last 1 hour (time_frame=1h, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "sol_1h")
         or check_text(r, ["volume"], tc, "sol_1h2")
         or check_text(r, ["wallet"], tc, "sol_1h3")
         or check_text(r, ["sol"], tc, "sol_1h4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: time_frame=7d
    results.append(await run_test(
        "TC-09",
        f"Use birdeye_token_top_traders to get the top BONK ({BONK}) traders over the last 7 days (time_frame=7d, sort_by=volume, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "bonk_7d")
         or check_text(r, ["volume"], tc, "bonk_7d2")
         or check_text(r, ["wallet"], tc, "bonk_7d3")
         or check_text(r, ["bonk"], tc, "bonk_7d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: time_frame=30d
    results.append(await run_test(
        "TC-10",
        f"Use birdeye_token_top_traders to get the top JUP ({JUP}) traders over 30 days (time_frame=30d, sort_by=volume, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "jup_30d")
         or check_text(r, ["volume"], tc, "jup_30d2")
         or check_text(r, ["wallet"], tc, "jup_30d3")
         or check_text(r, ["jup"], tc, "jup_30d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: limit=5
    results.append(await run_test(
        "TC-11",
        f"Use birdeye_token_top_traders to get the top 5 traders for RAY ({RAY}) in the last 24h (limit=5)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "ray_limit5")
         or check_text(r, ["volume"], tc, "ray_limit5b")
         or check_text(r, ["wallet"], tc, "ray_limit5c")
         or check_text(r, ["ray"], tc, "ray_limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: offset pagination
    results.append(await run_test(
        "TC-12",
        f"Use birdeye_token_top_traders to get the next page of top traders for SOL ({SOL}) (time_frame=24h, offset=10, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "sol_offset")
         or check_text(r, ["volume"], tc, "sol_offset2")
         or check_text(r, ["wallet"], tc, "sol_offset3")
         or check_text(r, ["sol"], tc, "sol_offset4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: sort_type=asc
    results.append(await run_test(
        "TC-13",
        f"Use birdeye_token_top_traders to get the lowest-volume traders for BONK ({BONK}) (sort_type=asc, sort_by=volume, time_frame=24h, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "bonk_asc")
         or check_text(r, ["volume"], tc, "bonk_asc2")
         or check_text(r, ["wallet"], tc, "bonk_asc3")
         or check_text(r, ["bonk"], tc, "bonk_asc4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language
    results.append(await run_test(
        "TC-14",
        f"Use birdeye_token_top_traders to show me who the top traders on BONK ({BONK}) are right now",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "natural_top_traders")
         or check_text(r, ["volume"], tc, "natural_top_traders2")
         or check_text(r, ["wallet"], tc, "natural_top_traders3")
         or check_text(r, ["bonk"], tc, "natural_top_traders4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: JUP by realized PnL 7d
    results.append(await run_test(
        "TC-15",
        f"Use birdeye_token_top_traders to get the most profitable JUP ({JUP}) traders by realized PnL over 7 days (sort_by=realized_pnl, time_frame=7d, limit=10)",
        {"birdeye_token_top_traders"},
        [lambda r, tc: check_text(r, ["trader"], tc, "jup_realized_7d")
         or check_text(r, ["pnl"], tc, "jup_realized_7d2")
         or check_text(r, ["profit"], tc, "jup_realized_7d3")
         or check_text(r, ["jup"], tc, "jup_realized_7d4")],
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

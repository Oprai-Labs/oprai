"""
Tests for GET /smart-money/v1/token/list via DeFi Query Service LLM.

TC-01  Default (1d, all traders)
TC-02  interval=7d
TC-03  interval=30d
TC-04  trader_style=risk_averse
TC-05  trader_style=risk_balancers
TC-06  trader_style=trenchers
TC-07  sort_by=net_flow
TC-08  sort_by=market_cap
TC-09  limit=5
TC-10  Natural language "what are smart money wallets buying"
TC-11  Natural language "tokens accumulating by smart traders in 7 days"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

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
    print("birdeye_smart_money_tokens (GET /smart-money/v1/token/list) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Default
    results.append(await run_test(
        "TC-01",
        "Use birdeye_smart_money_tokens to get the tokens smart money is accumulating today",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["smart"], tc, "default")
         or check_text(r, ["token"], tc, "default2")
         or check_text(r, ["trader"], tc, "default3")
         or check_text(r, ["flow"], tc, "default4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: interval=7d
    results.append(await run_test(
        "TC-02",
        "Use birdeye_smart_money_tokens to get smart money token picks over the last 7 days (interval=7d)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["smart"], tc, "7d")
         or check_text(r, ["token"], tc, "7d2")
         or check_text(r, ["7d"], tc, "7d3")
         or check_text(r, ["trader"], tc, "7d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: interval=30d
    results.append(await run_test(
        "TC-03",
        "Use birdeye_smart_money_tokens to get smart money token accumulation over 30 days (interval=30d)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["smart"], tc, "30d")
         or check_text(r, ["token"], tc, "30d2")
         or check_text(r, ["trader"], tc, "30d3")
         or check_text(r, ["flow"], tc, "30d4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: trader_style=risk_averse
    results.append(await run_test(
        "TC-04",
        "Use birdeye_smart_money_tokens to get tokens picked by risk-averse smart traders (trader_style=risk_averse)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["risk"], tc, "risk_averse")
         or check_text(r, ["smart"], tc, "risk_averse2")
         or check_text(r, ["token"], tc, "risk_averse3")
         or check_text(r, ["trader"], tc, "risk_averse4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: trader_style=risk_balancers
    results.append(await run_test(
        "TC-05",
        "Use birdeye_smart_money_tokens to get tokens accumulated by balanced risk traders (trader_style=risk_balancers)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["smart"], tc, "risk_bal")
         or check_text(r, ["token"], tc, "risk_bal2")
         or check_text(r, ["trader"], tc, "risk_bal3")
         or check_text(r, ["risk"], tc, "risk_bal4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: trader_style=trenchers
    results.append(await run_test(
        "TC-06",
        "Use birdeye_smart_money_tokens to get tokens that trenchers are buying (trader_style=trenchers)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["trencher"], tc, "trenchers")
         or check_text(r, ["smart"], tc, "trenchers2")
         or check_text(r, ["token"], tc, "trenchers3")
         or check_text(r, ["trader"], tc, "trenchers4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: sort_by=net_flow
    results.append(await run_test(
        "TC-07",
        "Use birdeye_smart_money_tokens to get tokens with the highest net flow from smart money (sort_by=net_flow)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["flow"], tc, "net_flow")
         or check_text(r, ["smart"], tc, "net_flow2")
         or check_text(r, ["token"], tc, "net_flow3")
         or check_text(r, ["trader"], tc, "net_flow4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: sort_by=market_cap
    results.append(await run_test(
        "TC-08",
        "Use birdeye_smart_money_tokens to get smart money tokens sorted by market cap (sort_by=market_cap)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["market"], tc, "mcap_sort")
         or check_text(r, ["smart"], tc, "mcap_sort2")
         or check_text(r, ["token"], tc, "mcap_sort3")
         or check_text(r, ["trader"], tc, "mcap_sort4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: limit=5
    results.append(await run_test(
        "TC-09",
        "Use birdeye_smart_money_tokens to get the top 5 tokens smart money is accumulating (limit=5)",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["smart"], tc, "limit5")
         or check_text(r, ["token"], tc, "limit5b")
         or check_text(r, ["trader"], tc, "limit5c")
         or check_text(r, ["flow"], tc, "limit5d")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: Natural language
    results.append(await run_test(
        "TC-10",
        "Use birdeye_smart_money_tokens to show me what tokens smart money wallets are buying right now",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["smart"], tc, "natural_buying")
         or check_text(r, ["token"], tc, "natural_buying2")
         or check_text(r, ["trader"], tc, "natural_buying3")
         or check_text(r, ["flow"], tc, "natural_buying4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: Natural language 7d accumulation
    results.append(await run_test(
        "TC-11",
        "Use birdeye_smart_money_tokens to find tokens being accumulated by smart traders over the last 7 days",
        {"birdeye_smart_money_tokens"},
        [lambda r, tc: check_text(r, ["smart"], tc, "natural_7d")
         or check_text(r, ["token"], tc, "natural_7d2")
         or check_text(r, ["trader"], tc, "natural_7d3")
         or check_text(r, ["flow"], tc, "natural_7d4")],
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

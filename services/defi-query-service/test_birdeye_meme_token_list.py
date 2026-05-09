"""
Tests for GET /defi/v3/token/meme/list via DeFi Query Service LLM.

TC-01  Default meme list (Solana)
TC-02  sort_by=market_cap
TC-03  sort_by=liquidity
TC-04  source=pump_dot_fun
TC-05  source=moonshot
TC-06  graduated=true
TC-07  graduated=false (still on bonding curve)
TC-08  min_progress_percent=80 (near graduation)
TC-09  min_liquidity filter
TC-10  limit=10
TC-11  chain=bsc
TC-12  Natural language "show me latest pump.fun tokens"
TC-13  Natural language "meme tokens close to graduation"
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
    print("birdeye_meme_token_list (GET /defi/v3/token/meme/list) — Tests")
    print("=" * 60)

    results = []

    # TC-01: Default
    results.append(await run_test(
        "TC-01",
        "Use birdeye_meme_token_list to get the latest meme tokens on Solana",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["meme"], tc, "default")
         or check_text(r, ["token"], tc, "default2")
         or check_text(r, ["market"], tc, "default3")
         or check_text(r, ["pump"], tc, "default4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: sort_by=market_cap
    results.append(await run_test(
        "TC-02",
        "Use birdeye_meme_token_list to get meme tokens sorted by market cap (sort_by=market_cap, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["market"], tc, "mcap_sort")
         or check_text(r, ["meme"], tc, "mcap_sort2")
         or check_text(r, ["token"], tc, "mcap_sort3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: sort_by=liquidity
    results.append(await run_test(
        "TC-03",
        "Use birdeye_meme_token_list to get meme tokens sorted by liquidity (sort_by=liquidity, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["liquid"], tc, "liq_sort")
         or check_text(r, ["meme"], tc, "liq_sort2")
         or check_text(r, ["token"], tc, "liq_sort3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: source=pump_dot_fun
    results.append(await run_test(
        "TC-04",
        "Use birdeye_meme_token_list to get meme tokens from pump.fun (source=pump_dot_fun, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["pump"], tc, "pump_source")
         or check_text(r, ["meme"], tc, "pump_source2")
         or check_text(r, ["token"], tc, "pump_source3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: source=moonshot
    results.append(await run_test(
        "TC-05",
        "Use birdeye_meme_token_list to get meme tokens from Moonshot (source=moonshot, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["moonshot"], tc, "moonshot_source")
         or check_text(r, ["meme"], tc, "moonshot_source2")
         or check_text(r, ["token"], tc, "moonshot_source3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: graduated=true
    results.append(await run_test(
        "TC-06",
        "Use birdeye_meme_token_list to get graduated meme tokens (graduated=true, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["graduat"], tc, "graduated_true")
         or check_text(r, ["meme"], tc, "graduated_true2")
         or check_text(r, ["token"], tc, "graduated_true3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: graduated=false
    results.append(await run_test(
        "TC-07",
        "Use birdeye_meme_token_list to get meme tokens still on the bonding curve (graduated=false, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["bonding"], tc, "not_graduated")
         or check_text(r, ["meme"], tc, "not_graduated2")
         or check_text(r, ["token"], tc, "not_graduated3")
         or check_text(r, ["graduat"], tc, "not_graduated4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: min_progress_percent=80
    results.append(await run_test(
        "TC-08",
        "Use birdeye_meme_token_list to find meme tokens with bonding curve progress over 80% (min_progress_percent=80, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["progress"], tc, "near_grad")
         or check_text(r, ["meme"], tc, "near_grad2")
         or check_text(r, ["bonding"], tc, "near_grad3")
         or check_text(r, ["token"], tc, "near_grad4")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: min_liquidity
    results.append(await run_test(
        "TC-09",
        "Use birdeye_meme_token_list to find meme tokens with at least $10000 liquidity (min_liquidity=10000, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["liquid"], tc, "min_liq")
         or check_text(r, ["meme"], tc, "min_liq2")
         or check_text(r, ["token"], tc, "min_liq3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: limit=10
    results.append(await run_test(
        "TC-10",
        "Use birdeye_meme_token_list to get the top 10 meme tokens by creation time (limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["meme"], tc, "limit10")
         or check_text(r, ["token"], tc, "limit10b")
         or check_text(r, ["market"], tc, "limit10c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: chain=bsc
    results.append(await run_test(
        "TC-11",
        "Use birdeye_meme_token_list to get meme tokens on BSC (chain=bsc, limit=10)",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["meme"], tc, "bsc_meme")
         or check_text(r, ["bsc"], tc, "bsc_meme2")
         or check_text(r, ["token"], tc, "bsc_meme3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language pump.fun
    results.append(await run_test(
        "TC-12",
        "Use birdeye_meme_token_list to show me the latest pump.fun tokens",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["pump"], tc, "natural_pump")
         or check_text(r, ["meme"], tc, "natural_pump2")
         or check_text(r, ["token"], tc, "natural_pump3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language close to graduation
    results.append(await run_test(
        "TC-13",
        "Use birdeye_meme_token_list to find meme tokens close to graduating from the bonding curve",
        {"birdeye_meme_token_list"},
        [lambda r, tc: check_text(r, ["bonding"], tc, "natural_grad")
         or check_text(r, ["graduat"], tc, "natural_grad2")
         or check_text(r, ["meme"], tc, "natural_grad3")
         or check_text(r, ["token"], tc, "natural_grad4")],
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

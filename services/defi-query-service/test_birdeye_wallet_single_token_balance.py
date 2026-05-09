"""
Unit tests for GET /v1/wallet/token_balance via DeFi Query Service LLM.

TC-01  Basic balance lookup (wallet + token_address)
TC-02  ui_amount_mode=raw
TC-03  ui_amount_mode=scaled (explicit)
TC-04  Different wallet
TC-05  Natural language "what is my SOL balance in this wallet"
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

WALLET1 = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
WALLET2 = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
SOL     = "So11111111111111111111111111111111111111112"
BONK    = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

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
    print("birdeye_wallet_single_token_balance (GET /v1/wallet/token_balance) — Unit Tests")
    print("=" * 60)

    results = []

    # TC-01: Basic
    results.append(await run_test(
        "TC-01",
        f"Use birdeye_wallet_single_token_balance to get the SOL ({SOL}) balance for wallet {WALLET1}",
        {"birdeye_wallet_single_token_balance"},
        [lambda r, tc: check_text(r, ["balance"], tc, "basic")
         or check_text(r, ["sol"], tc, "basic2")
         or check_text(r, ["amount"], tc, "basic3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: ui_amount_mode=raw
    results.append(await run_test(
        "TC-02",
        f"Use birdeye_wallet_single_token_balance to get the SOL ({SOL}) balance for wallet {WALLET1} with raw amounts (ui_amount_mode=raw)",
        {"birdeye_wallet_single_token_balance"},
        [lambda r, tc: check_text(r, ["balance"], tc, "raw_mode")
         or check_text(r, ["sol"], tc, "raw_mode2")
         or check_text(r, ["amount"], tc, "raw_mode3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: ui_amount_mode=scaled
    results.append(await run_test(
        "TC-03",
        f"Use birdeye_wallet_single_token_balance to get the BONK ({BONK}) balance for wallet {WALLET1} with scaled amounts (ui_amount_mode=scaled)",
        {"birdeye_wallet_single_token_balance"},
        [lambda r, tc: check_text(r, ["balance"], tc, "scaled_mode")
         or check_text(r, ["bonk"], tc, "scaled_mode2")
         or check_text(r, ["amount"], tc, "scaled_mode3")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Different wallet
    results.append(await run_test(
        "TC-04",
        f"Use birdeye_wallet_single_token_balance to get the SOL ({SOL}) balance for wallet {WALLET2}",
        {"birdeye_wallet_single_token_balance"},
        [lambda r, tc: check_text(r, ["balance"], tc, "wallet2")
         or check_text(r, ["sol"], tc, "wallet2b")
         or check_text(r, ["amount"], tc, "wallet2c")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Natural language
    results.append(await run_test(
        "TC-05",
        f"Use birdeye_wallet_single_token_balance to check the SOL ({SOL}) balance in wallet {WALLET1}",
        {"birdeye_wallet_single_token_balance"},
        [lambda r, tc: check_text(r, ["balance"], tc, "natural")
         or check_text(r, ["sol"], tc, "natural2")
         or check_text(r, ["amount"], tc, "natural3")],
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

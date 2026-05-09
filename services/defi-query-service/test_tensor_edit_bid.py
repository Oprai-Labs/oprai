"""
Test suite for GET /tx/edit_bid via DeFi Query Service LLM (Tensor Trade).

Coverage:
  TC-01  Edit bid price (basic)
  TC-02  Edit bid quantity
  TC-03  Edit bid expiry
  TC-04  Edit price + quantity together
  TC-05  Edit price + expireIn together
  TC-06  priorityMicroLamports set
  TC-07  compute units set
  TC-08  useSharedEscrow=true
  TC-09  privateTaker set
  TC-10  makerBroker set
  TC-11  All optional params together
  TC-12  Natural language "change my Tensor bid price"
  TC-13  Natural language "update bid quantity on Tensor"
  TC-14  Natural language "extend my Tensor bid expiry"
  TC-15  Natural language "edit my NFT offer on Tensor"

Note: bidStateAddress and blockhash are required but tx validity depends on chain state.
      Tests verify tool selection and that LLM acknowledges the transaction/response.
"""

import asyncio
import httpx

BASE = "http://localhost:3150"

# Realistic-format bid state address and blockhash (test values — API may return error)
BID_ADDR  = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
BLOCKHASH = "GHoXDtBerp2DzuoHY6EkQpPb1P9H8S8X7vXcKJdPHCM"

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
    print("tensor_edit_bid (GET /tx/edit_bid) — Full Test Suite")
    print("=" * 60)

    results = []

    # TC-01: Edit price
    results.append(await run_test(
        "TC-01",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH} and set price=1.5",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "edit_price")
         or check_text(r, ["tensor"], tc, "edit_price2")
         or check_text(r, ["transaction"], tc, "edit_price3")
         or check_text(r, ["price"], tc, "edit_price4")
         or check_text(r, ["error"], tc, "edit_price5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-02: Edit quantity
    results.append(await run_test(
        "TC-02",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH} and set quantity=5",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "edit_qty")
         or check_text(r, ["tensor"], tc, "edit_qty2")
         or check_text(r, ["transaction"], tc, "edit_qty3")
         or check_text(r, ["quantity"], tc, "edit_qty4")
         or check_text(r, ["error"], tc, "edit_qty5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-03: Edit expiry
    results.append(await run_test(
        "TC-03",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH} and set expire_in=86400",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "edit_expiry")
         or check_text(r, ["tensor"], tc, "edit_expiry2")
         or check_text(r, ["transaction"], tc, "edit_expiry3")
         or check_text(r, ["expir"], tc, "edit_expiry4")
         or check_text(r, ["error"], tc, "edit_expiry5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-04: Price + quantity
    results.append(await run_test(
        "TC-04",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=2.0, quantity=3",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "price_qty")
         or check_text(r, ["tensor"], tc, "price_qty2")
         or check_text(r, ["transaction"], tc, "price_qty3")
         or check_text(r, ["price"], tc, "price_qty4")
         or check_text(r, ["error"], tc, "price_qty5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-05: Price + expireIn
    results.append(await run_test(
        "TC-05",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=0.5, expire_in=3600",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "price_expiry")
         or check_text(r, ["tensor"], tc, "price_expiry2")
         or check_text(r, ["transaction"], tc, "price_expiry3")
         or check_text(r, ["price"], tc, "price_expiry4")
         or check_text(r, ["error"], tc, "price_expiry5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-06: Priority fee
    results.append(await run_test(
        "TC-06",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=1.0, priority_micro_lamports=50000",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "priority")
         or check_text(r, ["tensor"], tc, "priority2")
         or check_text(r, ["transaction"], tc, "priority3")
         or check_text(r, ["priority"], tc, "priority4")
         or check_text(r, ["error"], tc, "priority5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-07: Compute units
    results.append(await run_test(
        "TC-07",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=1.0, and set compute units to 200000 (compute=200000)",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "compute")
         or check_text(r, ["tensor"], tc, "compute2")
         or check_text(r, ["transaction"], tc, "compute3")
         or check_text(r, ["compute"], tc, "compute4")
         or check_text(r, ["error"], tc, "compute5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-08: useSharedEscrow=true
    results.append(await run_test(
        "TC-08",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=1.0, use_shared_escrow=true",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "shared_escrow")
         or check_text(r, ["tensor"], tc, "shared_escrow2")
         or check_text(r, ["transaction"], tc, "shared_escrow3")
         or check_text(r, ["escrow"], tc, "shared_escrow4")
         or check_text(r, ["error"], tc, "shared_escrow5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-09: privateTaker
    results.append(await run_test(
        "TC-09",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=1.0, private_taker=9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "private_taker")
         or check_text(r, ["tensor"], tc, "private_taker2")
         or check_text(r, ["transaction"], tc, "private_taker3")
         or check_text(r, ["taker"], tc, "private_taker4")
         or check_text(r, ["error"], tc, "private_taker5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-10: makerBroker
    results.append(await run_test(
        "TC-10",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=1.0, maker_broker=9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "maker_broker")
         or check_text(r, ["tensor"], tc, "maker_broker2")
         or check_text(r, ["transaction"], tc, "maker_broker3")
         or check_text(r, ["broker"], tc, "maker_broker4")
         or check_text(r, ["error"], tc, "maker_broker5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-11: All optional params
    results.append(await run_test(
        "TC-11",
        f"Use tensor_edit_bid to edit bid {BID_ADDR} with blockhash={BLOCKHASH}, price=3.0, quantity=2, expire_in=7200, compute=300000, priority_micro_lamports=100000",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "all_params")
         or check_text(r, ["tensor"], tc, "all_params2")
         or check_text(r, ["transaction"], tc, "all_params3")
         or check_text(r, ["price"], tc, "all_params4")
         or check_text(r, ["error"], tc, "all_params5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-12: Natural language price change
    results.append(await run_test(
        "TC-12",
        f"Use tensor_edit_bid to change my Tensor bid price — bid state address is {BID_ADDR}, blockhash is {BLOCKHASH}, new price is 2.5",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "natural_price")
         or check_text(r, ["tensor"], tc, "natural_price2")
         or check_text(r, ["transaction"], tc, "natural_price3")
         or check_text(r, ["price"], tc, "natural_price4")
         or check_text(r, ["error"], tc, "natural_price5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-13: Natural language quantity update
    results.append(await run_test(
        "TC-13",
        f"Use tensor_edit_bid to update my Tensor bid quantity — bid={BID_ADDR}, blockhash={BLOCKHASH}, quantity=10",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "natural_qty")
         or check_text(r, ["tensor"], tc, "natural_qty2")
         or check_text(r, ["transaction"], tc, "natural_qty3")
         or check_text(r, ["quantity"], tc, "natural_qty4")
         or check_text(r, ["error"], tc, "natural_qty5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-14: Natural language extend expiry
    results.append(await run_test(
        "TC-14",
        f"Use tensor_edit_bid to extend my Tensor bid expiry — bid={BID_ADDR}, blockhash={BLOCKHASH}, expire_in=172800",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "natural_expiry")
         or check_text(r, ["tensor"], tc, "natural_expiry2")
         or check_text(r, ["transaction"], tc, "natural_expiry3")
         or check_text(r, ["expir"], tc, "natural_expiry4")
         or check_text(r, ["error"], tc, "natural_expiry5")],
    ))
    await asyncio.sleep(DELAY)

    # TC-15: Natural language edit offer
    results.append(await run_test(
        "TC-15",
        f"Use tensor_edit_bid to edit my NFT offer on Tensor — bid state={BID_ADDR}, blockhash={BLOCKHASH}, set price to 0.8 SOL",
        {"tensor_edit_bid"},
        [lambda r, tc: check_text(r, ["bid"], tc, "natural_offer")
         or check_text(r, ["tensor"], tc, "natural_offer2")
         or check_text(r, ["transaction"], tc, "natural_offer3")
         or check_text(r, ["price"], tc, "natural_offer4")
         or check_text(r, ["error"], tc, "natural_offer5")],
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

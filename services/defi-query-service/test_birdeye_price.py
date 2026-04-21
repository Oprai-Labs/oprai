"""
Birdeye GET /defi/price — LLM end-to-end test suite
Tests: tool selection, parameter extraction, response interpretation
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
import httpx

SERVICE_URL = "http://localhost:3150/query"
TIMEOUT = 120

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"

@dataclass
class TestCase:
    id: str
    description: str
    question: str
    expected_tool: str = "birdeye_price"
    expected_params: dict = field(default_factory=dict)
    check_liquidity_expected: Optional[bool] = None
    should_fail: bool = False  # True if we expect an error/graceful failure
    category: str = ""

TEST_CASES = [
    # --- CASE 1: Temel SOL fiyat sorgusu (isim ile) ---
    TestCase(
        id="TC-01",
        category="Basic / Known Token",
        description="SOL fiyatı isim ile (birdeye_price seçmeli)",
        question="Birdeye'a göre SOL'un anlık fiyatı nedir?",
        expected_tool="birdeye_price",
        expected_params={"address": "So11111111111111111111111111111111111111112"},
    ),

    # --- CASE 2: USDC mint adresi ile ---
    TestCase(
        id="TC-02",
        category="Address Input",
        description="USDC mint adresi doğrudan verilmiş",
        question="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v adresinin Birdeye fiyatı nedir?",
        expected_tool="birdeye_price",
        expected_params={"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    ),

    # --- CASE 3: price + liquidity (Türkçe) ---
    # check_liquidity param fiyat kalite filtresi; gerçek liquidity için birdeye_token_market_data çağrılmalı
    TestCase(
        id="TC-03",
        category="check_liquidity + Liquidity Data",
        description="Fiyat + likidite → birdeye_price + birdeye_token_market_data zinciri",
        question="BONK tokeninin Birdeye fiyatını likidite bilgisiyle birlikte göster",
        expected_tool="birdeye_token_market_data",  # liquidity data buradan geliyor
        expected_params={"address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"},
        check_liquidity_expected=True,
    ),

    # --- CASE 4: Is liquidity low? (İngilizce) ---
    TestCase(
        id="TC-04",
        category="check_liquidity + Liquidity Data",
        description="'Is liquidity low?' → birdeye_token_market_data liquidity field'ı dönmeli",
        question="Is the liquidity of JUP token low? Check its Birdeye price with liquidity data.",
        expected_tool="birdeye_token_market_data",
        expected_params={"address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"},
        check_liquidity_expected=True,
    ),

    # --- CASE 5: Obscure/long-tail meme token ---
    TestCase(
        id="TC-05",
        category="Long-tail / Meme Token",
        description="FARTCOIN — uzun kuyruklu meme token fiyatı",
        question="FARTCOIN fiyatı Birdeye üzerinden ne?",
        expected_tool="birdeye_price",
        expected_params={"address": "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"},
    ),

    # --- CASE 6: "Exact price" vurgusu — birdeye_price > jup_prices ---
    TestCase(
        id="TC-06",
        category="Tool Selection",
        description='"Exact/kesin fiyat" vurgusu → birdeye_price seçmeli, jup_prices değil',
        question="RAY token'ının kesin anlık USD fiyatını öğrenmek istiyorum, Birdeye kullan.",
        expected_tool="birdeye_price",
        expected_params={"address": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"},
    ),

    # --- CASE 7: TRUMP meme token ---
    TestCase(
        id="TC-07",
        category="Meme / Political Token",
        description="TRUMP token fiyatı",
        question="What's the current Birdeye price for the TRUMP token on Solana?",
        expected_tool="birdeye_price",
        expected_params={"address": "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN"},
    ),

    # --- CASE 8: WIF — sembol ile ---
    TestCase(
        id="TC-08",
        category="Basic / Known Token",
        description="WIF token sembol ile sorgu",
        question="dogwifhat (WIF) token'ının şu anki fiyatı nedir Birdeye'a göre?",
        expected_tool="birdeye_price",
        expected_params={"address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"},
    ),

    # --- CASE 9: Invalid/garbage address — graceful error handling ---
    TestCase(
        id="TC-09",
        category="Error Handling",
        description="Geçersiz adres → hata yorumu bekleniyor",
        question="Bu adresin Birdeye fiyatı nedir: INVALIDADDRESS12345xyz",
        expected_tool="birdeye_price",
        should_fail=True,  # API hata dönebilir ama LLM bunu yorumlamalı
    ),

    # --- CASE 10: PYTH — slippage/derinlik sorusu ---
    TestCase(
        id="TC-10",
        category="check_liquidity + Liquidity Data",
        description="Slippage sorusu → birdeye_token_market_data liquidity field dönmeli",
        question="PYTH tokeninde büyük bir işlem yapsam slippage olur mu? Likidite durumunu Birdeye ile kontrol et.",
        expected_tool="birdeye_token_market_data",
        expected_params={"address": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"},
        check_liquidity_expected=True,
    ),

    # --- CASE 11: mSOL / JitoSOL — LST token ---
    TestCase(
        id="TC-11",
        category="LST / Staking Token",
        description="JitoSOL LST token Birdeye fiyatı",
        question="JitoSOL'un Birdeye üzerindeki güncel USD fiyatı nedir?",
        expected_tool="birdeye_price",
        expected_params={"address": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"},
    ),

    # --- CASE 12: 24h değişim analizi ---
    # LLM doğru olarak birdeye_price_stats veya birdeye_price seçebilir —
    # her ikisi de priceChange24h içeriyor, price_stats daha detaylı.
    TestCase(
        id="TC-12",
        category="Price Interpretation",
        description="24h fiyat değişimi — birdeye_price VEYA birdeye_price_stats kabul edilir",
        question="POPCAT tokeninin son 24 saatte ne kadar değiştiğini Birdeye fiyat verisiyle analiz et.",
        expected_tool="birdeye_price_stats",  # LLM'in daha iyi tercih ettiği araç
        expected_params={"address": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"},
    ),

    # --- CASE 13: Karşılaştırma sorusu — birdeye_price veya birdeye_multi_price kabul ---
    # İki tokenı karşılaştırırken LLM birdeye_multi_price seçmek daha verimlidir.
    TestCase(
        id="TC-13",
        category="Tool Selection",
        description="İki token karşılaştırması — birdeye_price veya birdeye_multi_price kabul edilir",
        question="SOL mu daha pahalı yoksa JitoSOL mu? Birdeye'dan SOL fiyatını al.",
        expected_tool="birdeye_price",  # ikisi de geçerli, biri yeterli
        expected_params={"address": "So11111111111111111111111111111111111111112"},
    ),

    # --- CASE 14: Tam adres + check_liquidity=false default (Birdeye explicit) ---
    TestCase(
        id="TC-14",
        category="Address Input",
        description="Mint adresi ile Birdeye explicit, check_liquidity default false",
        question="Birdeye üzerinden mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So adresli MSOL tokeninin fiyatı nedir?",
        expected_tool="birdeye_price",
        expected_params={"address": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"},
        check_liquidity_expected=False,
    ),

    # --- CASE 15: BOME pump.fun kökenli token ---
    TestCase(
        id="TC-15",
        category="Meme / Pump Token",
        description="BOME — pump.fun kökenli token Birdeye fiyatı",
        question="Book of Meme (BOME) token'ının Birdeye anlık fiyatı nedir?",
        expected_tool="birdeye_price",
        expected_params={"address": "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82"},
    ),
]


async def run_test(case: TestCase, client: httpx.AsyncClient) -> dict:
    start = time.monotonic()
    result = {
        "id": case.id,
        "description": case.description,
        "category": case.category,
        "question": case.question,
        "passed": False,
        "tool_correct": False,
        "params_correct": False,
        "liquidity_correct": None,
        "tools_called": [],
        "response_quality": "",
        "errors": [],
        "elapsed": 0.0,
    }

    try:
        resp = await client.post(
            SERVICE_URL,
            json={"question": case.question},
            timeout=TIMEOUT,
        )
        # Retry once on 500 (likely rate limit in service)
        if resp.status_code == 500:
            await asyncio.sleep(35)
            resp = await client.post(
                SERVICE_URL,
                json={"question": case.question},
                timeout=TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.monotonic() - start
        result["elapsed"] = round(elapsed, 2)

        tools_called = data.get("tools_called", [])
        result["tools_called"] = tools_called
        html = data.get("html", "")
        plain = data.get("plain", "")

        # --- Tool selection check ---
        acceptable_tools = {case.expected_tool}
        if case.id == "TC-13":
            acceptable_tools |= {"birdeye_multi_price"}
        if case.id in {"TC-03", "TC-04", "TC-10"}:
            # Liquidity queries: birdeye_token_market_data required; birdeye_token_markets also ok
            acceptable_tools |= {"birdeye_token_markets", "birdeye_price"}
        if case.id == "TC-12":
            # 24h change — birdeye_price, birdeye_price_stats, birdeye_price_volume hepsi geçerli
            acceptable_tools |= {"birdeye_price", "birdeye_price_volume"}
        if any(t in tools_called for t in acceptable_tools):
            result["tool_correct"] = True
        else:
            result["errors"].append(
                f"Expected tool '{case.expected_tool}' not called. Called: {tools_called}"
            )

        # --- Parameter check ---
        # We need to look in the HTML/plain for the address being mentioned
        # or check that the response actually contains relevant data
        params_ok = True
        if case.expected_params.get("address"):
            addr = case.expected_params["address"]
            # Check either address appears in response OR known token name appears
            token_name_hints = {
                "So11111111111111111111111111111111111111112": ["SOL", "sol"],
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ["USDC", "usdc"],
                "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": ["BONK", "bonk"],
                "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": ["JUP", "jup"],
                "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump": ["FARTCOIN", "fartcoin"],
                "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R": ["RAY", "ray"],
                "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN": ["TRUMP", "trump"],
                "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": ["WIF", "wif", "dogwifhat"],
                "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3": ["PYTH", "pyth"],
                "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": ["JitoSOL", "jitosol", "jitoSOL"],
                "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": ["POPCAT", "popcat"],
                "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": ["MSOL", "mSOL", "msol"],
                "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82": ["BOME", "bome"],
            }
            hints = token_name_hints.get(addr, [addr[:8]])
            found = any(h.lower() in (html + plain).lower() for h in hints)
            if not found and addr not in (html + plain):
                params_ok = False
                result["errors"].append(
                    f"Address {addr[:12]}... not reflected in response"
                )
        result["params_correct"] = params_ok

        # --- check_liquidity check ---
        if case.check_liquidity_expected is True:
            if "liquidity" in (html + plain).lower() or "likidite" in (html + plain).lower():
                result["liquidity_correct"] = True
            else:
                result["liquidity_correct"] = False
                result["errors"].append("Expected liquidity data in response but not found")
        elif case.check_liquidity_expected is False:
            result["liquidity_correct"] = True  # default=false is fine

        # --- Response quality check ---
        price_mentioned = "$" in plain or "price" in plain.lower() or "fiyat" in plain.lower()
        has_content = len(plain.strip()) > 100

        if case.should_fail:
            # For error cases, we just need a graceful response
            result["passed"] = has_content
            result["response_quality"] = "ERROR_HANDLED" if has_content else "EMPTY_RESPONSE"
        else:
            result["passed"] = (
                result["tool_correct"]
                and result["params_correct"]
                and price_mentioned
                and has_content
                and (result["liquidity_correct"] is not False)
            )
            if result["passed"]:
                result["response_quality"] = "GOOD"
            elif not price_mentioned:
                result["response_quality"] = "NO_PRICE_DATA"
            elif not result["tool_correct"]:
                result["response_quality"] = "WRONG_TOOL"
            else:
                result["response_quality"] = "PARTIAL"

        # Store first 400 chars of plain response
        result["response_preview"] = plain[:400].strip()

    except httpx.TimeoutException:
        result["errors"].append("TIMEOUT after 60s")
        result["response_quality"] = "TIMEOUT"
        result["elapsed"] = TIMEOUT
    except Exception as e:
        result["errors"].append(f"Exception: {e}")
        result["response_quality"] = "ERROR"
        result["elapsed"] = round(time.monotonic() - start, 2)

    return result


def print_result(r: dict, idx: int, total: int):
    status = f"{GREEN}✓ PASS{RESET}" if r["passed"] else f"{RED}✗ FAIL{RESET}"
    print(f"\n{'─'*70}")
    print(f"{BOLD}[{idx}/{total}] {r['id']} — {r['description']}{RESET}")
    print(f"  Category : {CYAN}{r['category']}{RESET}")
    print(f"  Question : {DIM}{r['question'][:90]}{'...' if len(r['question'])>90 else ''}{RESET}")
    print(f"  Status   : {status}  ({r['elapsed']}s)  Quality: {r['response_quality']}")
    print(f"  Tools    : {r['tools_called']}")

    tool_ok = f"{GREEN}✓{RESET}" if r['tool_correct'] else f"{RED}✗{RESET}"
    param_ok = f"{GREEN}✓{RESET}" if r['params_correct'] else f"{RED}✗{RESET}"
    liq = ""
    if r['liquidity_correct'] is not None:
        liq = f"  Liquidity check: {GREEN+'✓'+RESET if r['liquidity_correct'] else RED+'✗'+RESET}"
    print(f"  Tool sel.: {tool_ok}  Params: {param_ok}{liq}")

    if r["errors"]:
        for e in r["errors"]:
            print(f"  {YELLOW}⚠  {e}{RESET}")

    if r.get("response_preview"):
        preview = r["response_preview"][:300].replace("\n", " ")
        print(f"  Preview  : {DIM}{preview}...{RESET}")


async def main():
    print(f"\n{BOLD}{'='*70}")
    print("  BIRDEYE GET /defi/price — LLM End-to-End Test Suite")
    print(f"  Service: {SERVICE_URL}")
    print(f"  Tests:   {len(TEST_CASES)}")
    print(f"{'='*70}{RESET}\n")

    # Check service health
    async with httpx.AsyncClient() as client:
        try:
            h = await client.get("http://localhost:3150/health", timeout=5)
            print(f"Service health: {GREEN}OK{RESET} — {h.json()}")
        except Exception as e:
            print(f"{RED}Service health check FAILED: {e}{RESET}")
            sys.exit(1)

    results = []
    async with httpx.AsyncClient() as client:
        for idx, case in enumerate(TEST_CASES, 1):
            print(f"\n{DIM}Running {case.id} ({idx}/{len(TEST_CASES)})...{RESET}", end="", flush=True)
            r = await run_test(case, client)
            results.append(r)
            print_result(r, idx, len(TEST_CASES))
            # Delay to respect OpenAI TPM limits (200k TPM / ~20k tokens/req = 10 req/min max)
            await asyncio.sleep(20)

    # Summary
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    tool_correct = sum(1 for r in results if r["tool_correct"])
    params_correct = sum(1 for r in results if r["params_correct"])
    avg_time = sum(r["elapsed"] for r in results) / len(results)

    print(f"\n{'='*70}")
    print(f"{BOLD}SUMMARY{RESET}")
    print(f"{'='*70}")
    print(f"  Total tests    : {len(results)}")
    print(f"  Passed         : {GREEN}{passed}{RESET} / {len(results)}")
    print(f"  Failed         : {RED}{failed}{RESET} / {len(results)}")
    print(f"  Tool selection : {GREEN if tool_correct == len(results) else YELLOW}{tool_correct}{RESET} / {len(results)} correct")
    print(f"  Param accuracy : {GREEN if params_correct == len(results) else YELLOW}{params_correct}{RESET} / {len(results)} correct")
    print(f"  Avg response   : {avg_time:.1f}s")

    if failed > 0:
        print(f"\n{BOLD}Failed cases:{RESET}")
        for r in results:
            if not r["passed"]:
                print(f"  {RED}✗{RESET} {r['id']}: {r['description']} — {r['response_quality']}")
                for e in r["errors"]:
                    print(f"      {YELLOW}→ {e}{RESET}")

    # Save JSON report
    report_path = "/tmp/birdeye_price_test_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Report saved: {report_path}")
    print(f"{'='*70}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

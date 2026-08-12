"""
Kapsamlı kullanıcı case kalite testi.
LLM routing, analiz kalitesi, hata yönetimi, çok dilli destek.
"""
import asyncio
import httpx
import time

BASE = "http://localhost:3150"
DELAY = 18      # rate limit koruması
TIMEOUT = 90

# ─── renk ───────────────────────────────────────────────────────────────────
G = "\033[92m"  # green
R = "\033[91m"  # red
Y = "\033[93m"  # yellow
B = "\033[94m"  # blue
W = "\033[97m"  # white
D = "\033[90m"  # dim
E = "\033[0m"   # reset
BOLD = "\033[1m"

results = {"pass": 0, "fail": 0, "skip": 0}
failures = []

async def ask(question: str) -> dict | None:
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.post(f"{BASE}/query", json={"question": question})
                if r.status_code == 500 and attempt == 0:
                    print(f"  {Y}⚠ 500 — retry 30s{E}")
                    await asyncio.sleep(30)
                    continue
                r.raise_for_status()
                return r.json()
        except Exception as ex:
            if attempt == 0:
                await asyncio.sleep(30)
            else:
                return {"error": str(ex), "html": "", "plain": "", "tools_called": []}
    return None

def check(tc, label, ok, detail=""):
    if ok:
        print(f"  {G}✅ [{tc}] {label}{E}" + (f" {D}— {detail}{E}" if detail else ""))
        results["pass"] += 1
    else:
        print(f"  {R}❌ [{tc}] {label}{E}" + (f" {D}— {detail}{E}" if detail else ""))
        results["fail"] += 1
        failures.append(f"[{tc}] {label}: {detail}")

def tool_ok(called, want_any):
    return bool(set(called) & set(want_any))

def has_words(plain, words):
    low = plain.lower()
    return [w for w in words if w.lower() not in low]

def word_count(plain):
    return len(plain.split())

def no_error(plain):
    """LLM response'da ham hata kodu/stack olmamalı."""
    bad = ["internal server error", "traceback", "exception:", "attributeerror", "typeerror"]
    low = plain.lower()
    found = [b for b in bad if b in low]
    return found

async def run(tc, question, want_tools=None, want_words=None, min_words=0, no_tool=False):
    print(f"\n{D}{'─'*65}{E}")
    print(f"{W}[{tc}]{E} {question[:80]}")

    d = await ask(question)
    if d is None or "error" in d:
        check(tc, "request_ok", False, d.get("error","") if d else "None")
        results["skip"] += 1
        return d

    plain = d.get("plain", "")
    called = d.get("tools_called", [])
    print(f"  {D}tools: {called}{E}")

    if no_tool:
        check(tc, "no_tool_called", not called, f"called={called}")
    elif want_tools:
        check(tc, "tool_routing", tool_ok(called, want_tools),
              f"called={called}, want_any={want_tools}")

    if want_words:
        missing = has_words(plain, want_words)
        check(tc, "content_keywords", not missing, f"missing={missing}")

    if min_words:
        wc = word_count(plain)
        check(tc, "response_length", wc >= min_words, f"{wc} words (min {min_words})")

    bad = no_error(plain)
    check(tc, "no_raw_error", not bad, f"found={bad}" if bad else "")

    return d

# ═══════════════════════════════════════════════════════════════════════════
async def main():
    print(f"\n{BOLD}{'═'*65}")
    print("  Kullanıcı Senaryoları — Routing & Kalite Testi")
    print(f"{'═'*65}{E}\n")

    h = await httpx.AsyncClient().get(f"{BASE}/health")
    print(f"  Servis: {h.json()}")

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 1: Temel DeFi Soruları (Solend) ━━━{E}")

    await run("S01", "Solend'de SOL yatırırsam ne kadar faiz kazanırım?",
              want_tools=["solend_reserves"], want_words=["sol", "apy", "%"], min_words=40)
    await asyncio.sleep(DELAY)

    await run("S02", "Solend toplam TVL ne kadar, sağlıklı mı?",
              want_tools=["solend_stats"], want_words=["tvl", "borrow", "deposit"], min_words=30)
    await asyncio.sleep(DELAY)

    await run("S03", "Solend'de bugün kaç kişi borç aldı?",
              want_tools=["solend_daily_stats"], want_words=["borrower", "depositor"], min_words=20)
    await asyncio.sleep(DELAY)

    await run("S04", "Solend'de likidasyon riski olan pozisyonlar var mı?",
              want_tools=["solend_liquidation_attempts", "solend_obligations_filtered"], min_words=30)
    await asyncio.sleep(DELAY)

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 2: Cüzdan / Kullanıcı Sorgular ━━━{E}")

    WALLET = "CakcnaRDHka2gXyfbEd2d3xsvkJkqsLw2akB3zsN1D2S"

    await run("W01", f"{WALLET} cüzdanının Solend pozisyonlarını göster",
              want_tools=["solend_user_overview"], want_words=["deposit", "borrow"], min_words=20)
    await asyncio.sleep(DELAY)

    await run("W02", f"{WALLET} likidasyon riskinde mi?",
              want_tools=["solend_user_overview"], min_words=20)
    await asyncio.sleep(DELAY)

    await run("W03", f"{WALLET} Solend ödüllerini claim edebilir mi?",
              want_tools=["solend_confirmed_rewards"], min_words=20)
    await asyncio.sleep(DELAY)

    await run("W04", f"Yanlış bir cüzdan: XXXXINVALID cüzdanının Solend pozisyonları",
              want_tools=["solend_user_overview"], min_words=10)
    await asyncio.sleep(DELAY)

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 3: Karşılaştırmalı Analiz ━━━{E}")

    await asyncio.sleep(DELAY)

    await asyncio.sleep(DELAY)

    await run("C03", "Solend ve Kamino'nun risk profilleri nasıl?",
              want_tools=["solend_reserves", "kamino_market_reserves"], min_words=50)
    await asyncio.sleep(DELAY)

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 4: Jupiter / Swap ━━━{E}")

    await run("J01", "1000 USDC ile kaç SOL alırım?",
              want_tools=["jup_quote"], want_words=["sol", "usdc"], min_words=20)
    await asyncio.sleep(DELAY)

    await run("J02", "BONK token fiyatı nedir?",
              want_tools=["jup_prices", "birdeye_price"], want_words=["bonk"], min_words=10)
    await asyncio.sleep(DELAY)

    await run("J03", "Son çıkan Solana token'ları neler?",
              want_tools=["jup_new_tokens", "birdeye_new_listings"], min_words=20)
    await asyncio.sleep(DELAY)

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 5: Piyasa Verileri ━━━{E}")

    await run("M01", "SOL/USDC günlük işlem hacmi nedir?",
              want_tools=["birdeye_price_volume", "dex_search"], min_words=20)
    await asyncio.sleep(DELAY)

    await run("M02", "En yüksek hacimli Solana token'larını listele",
              want_tools=["birdeye_token_list", "birdeye_token_market_data"], min_words=30)
    await asyncio.sleep(DELAY)

    await run("M03", "Jito mevcut tip floor nedir?",
              want_tools=["jito_tip_floor"], want_words=["jito", "tip"], min_words=10)
    await asyncio.sleep(DELAY)

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 6: Hata / Edge Case Yönetimi ━━━{E}")

    await run("E01", "Solend'de 0x1234abcd mint adresinin fiyatı nedir?",
              want_tools=["solend_prices", "jup_prices", "birdeye_price"], min_words=10)
    await asyncio.sleep(DELAY)

    # LLM bilgisiyle yanıtlamalı, tool çağırmamalı
    await run("E02", "DeFi lending nedir, nasıl çalışır?",
              no_tool=True, min_words=50)
    await asyncio.sleep(DELAY)

    await run("E03", "Solend'de limit order verebilir miyim?",
              no_tool=True, min_words=15)
    await asyncio.sleep(DELAY)

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 7: Türkçe Kullanıcı ━━━{E}")

    await run("T01", "Solend'de en iyi faiz oranları hangi varlıklarda?",
              want_tools=["solend_reserves"], want_words=["apy", "%"], min_words=30)
    await asyncio.sleep(DELAY)

    await run("T02", "Bugün Solend'de kaç işlem yapıldı ve toplam ücretler ne kadar?",
              want_tools=["solend_daily_stats", "solend_daily_fees"], min_words=20)
    await asyncio.sleep(DELAY)

    await run("T03", "Solend ödüllerini nasıl claim ederim?",
              no_tool=True, min_words=20)
    await asyncio.sleep(DELAY)

    # ────────────────────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 8: Derinlemesine Analiz ━━━{E}")

    await run("A01", "Solend protokolü hakkında kapsamlı bir analiz yap: TVL, rezervler, ödüller, riskler",
              want_tools=["solend_stats", "solend_reserves"],
              want_words=["tvl", "apy", "risk"], min_words=80)
    await asyncio.sleep(DELAY)

    await run("A02", "Şu an Solana'da hangi yield fırsatı en iyi? Solend, Kamino, Marinade karşılaştır",
              want_tools=["solend_reserves", "kamino_market_reserves"],
              min_words=60)
    await asyncio.sleep(DELAY)

    # ════════════════════════════════════════════════════════════════════════
    total = results["pass"] + results["fail"]
    pct = int(results["pass"] / total * 100) if total else 0

    print(f"\n{BOLD}{'═'*65}")
    print(f"  SONUÇ: {G}{results['pass']} PASS{E}  {R}{results['fail']} FAIL{E}  {Y}{results['skip']} SKIP{E}  — {BOLD}{pct}%{E}")
    print(f"{'═'*65}{E}")

    if failures:
        print(f"\n{R}{BOLD}  Başarısız testler:{E}")
        for f in failures:
            print(f"  {R}• {f}{E}")

    print()

if __name__ == "__main__":
    asyncio.run(main())

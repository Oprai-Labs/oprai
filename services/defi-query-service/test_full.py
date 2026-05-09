"""
Kapsamlı entegrasyon testi — tüm protokoller, routing, kalite, hata yönetimi.
"""
import asyncio, httpx, time, sys

BASE    = "http://localhost:3150"
DELAY   = 16   # saniye — rate limit koruması
TIMEOUT = 90

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"
W="\033[97m"; D="\033[90m"; E="\033[0m";  BOLD="\033[1m"

stats   = {"pass": 0, "fail": 0, "skip": 0}
fails   = []

# ── helpers ──────────────────────────────────────────────────────────────────

async def ask(q: str) -> dict | None:
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.post(f"{BASE}/query", json={"question": q})
                if r.status_code == 500 and attempt == 0:
                    print(f"  {Y}⚠ service 500 — retry 30s{E}")
                    await asyncio.sleep(30); continue
                r.raise_for_status()
                return r.json()
        except Exception as ex:
            if attempt == 0: await asyncio.sleep(30)
            else: return {"__err": str(ex), "html":"", "plain":"", "tools_called":[]}
    return None

def ok(tc, label, passed, detail=""):
    sym = f"{G}✅{E}" if passed else f"{R}❌{E}"
    print(f"  {sym} [{tc}] {label}" + (f"  {D}{detail}{E}" if detail else ""))
    if passed: stats["pass"] += 1
    else:
        stats["fail"] += 1
        fails.append(f"[{tc}] {label}: {detail}")

def has(plain, *words):
    lo = plain.lower()
    return [w for w in words if w.lower() not in lo]

def tools_hit(called, *want):
    return bool(set(called) & set(want))

def no_crash(plain):
    bad = ["internal server error","traceback","exception:","attributeerror","temporarily unavailable"]
    lo  = plain.lower()
    return [b for b in bad if b in lo]

async def run(tc, question,
              want=None,        # any of these tools must be called
              keywords=(),      # words that must appear in plain text
              min_w=0,          # minimum word count
              no_tool=False,    # assert NO tool called
              bad_tools=()):    # assert NONE of these are called
    print(f"\n{D}{'─'*66}{E}")
    print(f"{W}[{tc}]{E} {question[:90]}")

    d = await ask(question)
    if d is None or "__err" in (d or {}):
        ok(tc, "request_ok", False, d.get("__err","") if d else "None")
        stats["skip"] += 1
        return {}

    plain  = d.get("plain","")
    called = d.get("tools_called",[])
    print(f"  {D}tools: {called}{E}")

    if no_tool:
        ok(tc, "no_tool_called", not called, f"called={called}")
    elif want:
        ok(tc, "tool_routing", tools_hit(called, *want),
           f"called={called}  want_any={list(want)}")

    if bad_tools:
        hit = set(called) & set(bad_tools)
        ok(tc, "bad_tool_absent", not hit, f"should not call {hit}")

    if keywords:
        missing = has(plain, *keywords)
        ok(tc, "keywords", not missing, f"missing={missing}")

    if min_w:
        wc = len(plain.split())
        ok(tc, "length", wc >= min_w, f"{wc} words (min {min_w})")

    crashes = no_crash(plain)
    ok(tc, "no_crash", not crashes, str(crashes) if crashes else "")

    return d

# ═══════════════════════════════════════════════════════════════════════════
WALLET = "CakcnaRDHka2gXyfbEd2d3xsvkJkqsLw2akB3zsN1D2S"

async def main():
    print(f"\n{BOLD}{'═'*66}")
    print("  TAM ENTEGRASYON TEST SÜİTİ")
    print(f"{'═'*66}{E}")
    h = await httpx.AsyncClient().get(f"{BASE}/health")
    print(f"  {h.json()}\n")

    # ── 1. JUPITER ────────────────────────────────────────────────────────
    print(f"{B}{BOLD}━━━ 1. Jupiter ━━━{E}")

    await run("JUP-01", "1000 USDC ile kaç SOL alırım?",
              want=["jup_quote"], keywords=["sol","usdc"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("JUP-02", "500 USDT ile kaç BONK alırım?",
              want=["jup_quote"], keywords=["bonk","usdt"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("JUP-03", "Raydium'da 200 SOL ile kaç USDC alırım?",
              want=["raydium_swap_quote"], bad_tools=["jup_quote"], keywords=["usdc","sol"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("JUP-04", "BONK token fiyatı nedir?",
              want=["jup_prices","birdeye_price"], keywords=["bonk"], min_w=10)
    await asyncio.sleep(DELAY)

    await run("JUP-05", "Son çıkan Solana token'larını listele",
              want=["jup_recent","birdeye_new_listings"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("JUP-06", "SOL ve USDC ve WIF mint adreslerini bul",
              want=["jup_search","jup_prices"], keywords=["sol","usdc"], min_w=10)
    await asyncio.sleep(DELAY)

    # ── 2. SOLEND ─────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 2. Solend ━━━{E}")

    await run("SOL-01", "Solend'de SOL supply APY nedir?",
              want=["solend_reserves"], keywords=["sol","apy"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("SOL-02", "Solend TVL ve toplam borç miktarı",
              want=["solend_stats"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("SOL-03", "Solend bugünkü istatistikler",
              want=["solend_daily_stats"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("SOL-04", f"{WALLET} Solend pozisyonları",
              want=["solend_user_overview"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("SOL-05", f"{WALLET} Solend likidasyon riskini analiz et",
              want=["solend_user_overview"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("SOL-06", f"{WALLET} Solend ödülleri ne kadar?",
              want=["solend_confirmed_rewards"], min_w=10)
    await asyncio.sleep(DELAY)

    await run("SOL-07", "Solend LST oranları — jitoSOL, mSOL, bSOL",
              want=["solend_lst_rates"], keywords=["sol"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("SOL-08", "Solend son duyuruları neler?",
              want=["solend_announcements","solend_changelogs"], min_w=10)
    await asyncio.sleep(DELAY)

    await run("SOL-09", "Solend referral programı hakkında bilgi ver",
              want=["solend_referral_stats","solend_referral_payments"], min_w=10)
    await asyncio.sleep(DELAY)

    await run("SOL-10", "Solend points leaderboard",
              want=["solend_points_leaderboard","solend_points"], min_w=10)
    await asyncio.sleep(DELAY)

    # ── 3. KAMINO ─────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 3. Kamino ━━━{E}")

    await run("KAM-01", "Kamino'da USDC supply APY nedir?",
              want=["kamino_market_reserves"], keywords=["usdc"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("KAM-02", f"{WALLET} Kamino pozisyonları",
              want=["kamino_user_positions","kamino_user_obligations"], min_w=10)
    await asyncio.sleep(DELAY)

    await run("KAM-03", "Kamino'daki en iyi kazanç vault'ları hangileri?",
              want=["kamino_earn_vaults"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("KAM-04", "Kamino staking yield'leri — jitoSOL, mSOL",
              want=["kamino_staking_yields"], keywords=["sol"], min_w=15)
    await asyncio.sleep(DELAY)

    # ── 4. MARGINFI ───────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 4. MarginFi ━━━{E}")

    await run("MFI-01", "MarginFi'da SOL ve USDC APY'si nedir?",
              want=["marginfi_banks"], keywords=["sol","usdc"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("MFI-02", "MarginFi LST oranları",
              want=["marginfi_lst_rates","marginfi_staked_banks"], min_w=15)
    await asyncio.sleep(DELAY)

    # ── 5. KARŞILAŞTIRMAli ANALİZ ────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 5. Karşılaştırmalı Analiz ━━━{E}")

    await run("CMP-01", "USDC için en yüksek lending APY: Solend, Kamino, MarginFi karşılaştır",
              want=["solend_reserves","kamino_market_reserves","marginfi_banks"],
              keywords=["usdc","apy"], min_w=50)
    await asyncio.sleep(DELAY)

    await run("CMP-02", "SOL staking için en iyi seçenek: Marinade, Jito, BlazeStake?",
              want=["marinade_msol_apy","jito_jitosol_ratio","jito_stake_pool_stats"], min_w=40)
    await asyncio.sleep(DELAY)

    await run("CMP-03", "Solend ve Kamino risk profili farkları neler?",
              want=["solend_reserves","kamino_market_reserves"], min_w=60)
    await asyncio.sleep(DELAY)

    # ── 6. MARINADE / STAKING ─────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 6. Marinade / Staking ━━━{E}")

    await run("MAR-01", "Marinade mSOL APY nedir?",
              want=["marinade_msol_apy"], keywords=["msol","apy"], min_w=10)
    await asyncio.sleep(DELAY)

    await run("MAR-02", "Marinade TVL ve protokol istatistikleri",
              want=["marinade_stats"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("MAR-03", "Marinade en iyi validator'ları listele",
              want=["marinade_validators","marinade_validator_scores"], min_w=20)
    await asyncio.sleep(DELAY)

    # ── 7. JITO ───────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 7. Jito ━━━{E}")

    await run("JIT-01", "Jito şu anki tip floor nedir?",
              want=["jito_tip_floor"], keywords=["jito"], min_w=10)
    await asyncio.sleep(DELAY)

    await run("JIT-02", "Jito MEV ödülleri bu hafta",
              want=["jito_mev_rewards","jito_daily_mev"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("JIT-03", "JitoSOL APY ve stake oranı",
              want=["jito_jitosol_ratio","jito_stake_pool_stats"], min_w=15)
    await asyncio.sleep(DELAY)

    # ── 8. RAYDIUM ────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 8. Raydium ━━━{E}")

    await run("RAY-01", "Raydium'da SOL-USDC havuzunun TVL ve APR'si nedir?",
              want=["raydium_pool_by_mint","raydium_pools"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("RAY-02", "Raydium'da 200 SOL ile kaç USDC alırım?",
              want=["raydium_swap_quote"], keywords=["usdc","sol"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("RAY-03", "En yüksek hacimli Raydium havuzları",
              want=["raydium_pools"], min_w=20)
    await asyncio.sleep(DELAY)

    # ── 9. BIRDEYE ────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 9. Birdeye ━━━{E}")

    await run("BRD-01", "SOL son 24 saatteki fiyat değişimi ve hacmi",
              want=["birdeye_price_volume","birdeye_token_overview"], keywords=["sol"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("BRD-02", "BONK token güvenlik analizi — rug riski var mı?",
              want=["birdeye_token_security","token_security"], keywords=["bonk"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("BRD-03", "En yüksek hacimli Solana token'ları bugün",
              want=["birdeye_token_list","birdeye_token_trade_data","birdeye_token_trending",
                    "jup_trending"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("BRD-04", f"{WALLET} cüzdanının portföyü nedir?",
              want=["birdeye_wallet_token_list","helius_wallet_tokens","wallet_balance",
                    "birdeye_wallet_current_net_worth"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("BRD-05", f"{WALLET} son işlemleri göster",
              want=["birdeye_wallet_tx_list","helius_wallet_txs","user_transactions"], min_w=15)
    await asyncio.sleep(DELAY)

    # ── 10. DEXSCREENER ───────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 10. DexScreener ━━━{E}")

    await run("DEX-01", "DexScreener'da en çok boost edilmiş token'lar",
              want=["dex_trending"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("DEX-02", "BONK token DexScreener'daki havuzları",
              want=["dex_search","dex_token"], keywords=["bonk"], min_w=15)
    await asyncio.sleep(DELAY)

    # ── 11. HELIUS ────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 11. Helius ━━━{E}")

    await run("HEL-01", "BONK token holder sayısı ve dağılımı",
              want=["helius_token_holders","birdeye_token_holders",
                    "birdeye_holder_profile","birdeye_holder_distribution"], keywords=["bonk"], min_w=15)
    await asyncio.sleep(DELAY)

    await run("HEL-02", f"{WALLET} cüzdanındaki token'lar",
              want=["helius_wallet_tokens","birdeye_wallet_token_list",
                    "birdeye_wallet_current_net_worth"], min_w=15)
    await asyncio.sleep(DELAY)

    # ── 12. ORCA ──────────────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 12. Orca ━━━{E}")

    await run("ORC-01", "Orca'daki en yüksek TVL'li havuzlar",
              want=["orca_pools","orca_pools_search"], min_w=20)
    await asyncio.sleep(DELAY)

    await run("ORC-02", "Orca protokol genel istatistikleri",
              want=["orca_protocol_stats"], min_w=15)
    await asyncio.sleep(DELAY)

    # ── 13. HATA / GUARD YÖNETİMİ ────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 13. Hata & Guard ━━━{E}")

    await run("ERR-01", "XXXXINVALID cüzdanının Solend pozisyonları",
              min_w=10)  # tool çağırabilir ya da reddedebilir, ikisi de OK
    await asyncio.sleep(DELAY)

    await run("ERR-02", "0x1234abcd adresinin token fiyatı",
              min_w=10)  # Ethereum adresi — nazik red
    await asyncio.sleep(DELAY)

    await run("ERR-03", "Solana'da limit order nerede verilir?",
              no_tool=True, min_w=15)
    await asyncio.sleep(DELAY)

    await run("ERR-04", "DeFi lending nedir, nasıl çalışır?",
              no_tool=True, min_w=40)
    await asyncio.sleep(DELAY)

    await run("ERR-05", "1 SOL kaç dolar? Şu anki fiyat",
              want=["jup_prices","birdeye_price"], keywords=["sol"], min_w=10)
    await asyncio.sleep(DELAY)

    # ── 14. TÜRKÇE & ÇOK DİLLİ ───────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 14. Türkçe Kullanıcı ━━━{E}")

    await run("TUR-01", "Solend'e USDC yatırmak mantıklı mı, APY ve ödülleri neler?",
              want=["solend_reserves","solend_reward_stats"], min_w=40)
    await asyncio.sleep(DELAY)

    await run("TUR-02", "Şu an Solana'da en yüksek getiri nerede?",
              want=["solend_reserves","kamino_market_reserves","marginfi_banks",
                    "kamino_strategies","kamino_staking_yields","kamino_earn_vaults",
                    "solend_lst_rates","marginfi_lst_rates","jito_stake_pool_stats"], min_w=40)
    await asyncio.sleep(DELAY)

    await run("TUR-03", "BONK almalı mıyım? Fiyat, güvenlik ve hacim analizi",
              want=["jup_prices","birdeye_token_security","birdeye_price_volume","token_security"],
              keywords=["bonk"], min_w=50)
    await asyncio.sleep(DELAY)

    await run("TUR-04", "Marinade ile SOL stake etmenin avantajları neler?",
              want=["marinade_msol_apy","marinade_stats"], min_w=40)
    await asyncio.sleep(DELAY)

    # ── 15. DERİN ANALİZ ─────────────────────────────────────────────────
    print(f"\n{B}{BOLD}━━━ 15. Derin Analiz ━━━{E}")

    await run("ANA-01",
              "Solend protokolü kapsamlı analiz: TVL, rezervler, ödüller, riskler, genel sağlık",
              want=["solend_stats","solend_reserves"],
              keywords=["tvl","apy"], min_w=80)
    await asyncio.sleep(DELAY)

    await run("ANA-02",
              "Solana DeFi lending ekosistemi genel bakış: Solend, Kamino, MarginFi karşılaştırması",
              want=["solend_reserves","kamino_market_reserves","marginfi_banks"],
              min_w=80)
    await asyncio.sleep(DELAY)

    await run("ANA-03",
              f"{WALLET} cüzdanı tam analiz: portföy, Solend pozisyonları, likidasyon riski",
              want=["solend_user_overview"],
              min_w=40)
    await asyncio.sleep(DELAY)

    # ─── ÖZET ─────────────────────────────────────────────────────────────
    total = stats["pass"] + stats["fail"]
    pct   = int(stats["pass"] / total * 100) if total else 0
    color = G if pct >= 90 else (Y if pct >= 75 else R)

    print(f"\n{BOLD}{'═'*66}")
    print(f"  SONUÇ  {color}{stats['pass']} PASS{E}  {R}{stats['fail']} FAIL{E}  "
          f"{Y}{stats['skip']} SKIP{E}  —  {BOLD}{color}{pct}%{E}")
    print(f"{'═'*66}{E}")

    if fails:
        print(f"\n{R}{BOLD}  Başarısız:{E}")
        for f in fails:
            print(f"  {R}• {f}{E}")
    print()

if __name__ == "__main__":
    asyncio.run(main())

"""
Stress test round 2 — different scenarios, adversarial inputs, rephrased intents,
cross-protocol combos, ambiguous queries, multilingual, edge cases.
Report failures only. Parallel execution (5 concurrent).
"""
import asyncio, httpx, time, sys

BASE        = "http://localhost:3150"
TIMEOUT     = 120
CONCURRENCY = 5

R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
W="\033[97m"; D="\033[90m"; E="\033[0m";  BOLD="\033[1m"

stats = {"pass": 0, "fail": 0}
fails = []
lock  = asyncio.Lock()

# ── core helpers ──────────────────────────────────────────────────────────────

async def ask(q: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                    r = await c.post(f"{BASE}/query", json={"question": q})
                    if r.status_code in (429, 500) and attempt < 2:
                        await asyncio.sleep(25 + attempt * 25)
                        continue
                    r.raise_for_status()
                    return r.json()
            except Exception as ex:
                if attempt < 2: await asyncio.sleep(25)
                else: return {"__err": str(ex), "plain": "", "tools_called": []}
        return {"__err": "max_retries", "plain": "", "tools_called": []}

def hit(called, *want):     return bool(set(called) & set(want))
def miss_words(plain, *kw): lo = plain.lower(); return [w for w in kw if w.lower() not in lo]
def wc(plain):              return len(plain.split())
def has_num(plain):         return any(c.isdigit() for c in plain)
def raw_err(plain):
    bads = ["internal server error","traceback","exception:","temporarily unavailable","attributeerror"]
    lo = plain.lower(); return [b for b in bads if b in lo]

async def run(tc, q, sem, want=(), kw=(), min_w=0, no_tool=False, has_n=False, bad_tools=()):
    d      = await ask(q, sem)
    plain  = d.get("plain","")
    called = d.get("tools_called",[])
    errs   = []

    if d.get("__err"):
        errs.append(f"request_error={d['__err']}")
    else:
        if no_tool and called:
            errs.append(f"expected_no_tool called={called}")
        elif want and not hit(called, *want):
            errs.append(f"tool_miss: called={called} want_any={list(want)}")
        if bad_tools and hit(called, *bad_tools):
            errs.append(f"bad_tool_called: {[t for t in called if t in bad_tools]}")
        if kw:
            m = miss_words(plain, *kw)
            if m: errs.append(f"missing_keywords={m}")
        if min_w and wc(plain) < min_w:
            errs.append(f"too_short={wc(plain)}<{min_w}")
        if has_n and not has_num(plain):
            errs.append("no_number_in_response")
        re = raw_err(plain)
        if re: errs.append(f"raw_error={re}")

    async with lock:
        if not errs:
            stats["pass"] += 1
            print(f"{G}✓{E} [{tc}]")
        else:
            stats["fail"] += 1
            detail = " | ".join(errs)
            fails.append(f"[{tc}] {q[:65]!r}  →  {detail}")
            print(f"{R}✗{E} [{tc}] {detail[:110]}")

# ═══════════════════════════════════════════════════════════════════════════
WALLET  = "CakcnaRDHka2gXyfbEd2d3xsvkJkqsLw2akB3zsN1D2S"
WALLET2 = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"  # Raydium fee wallet
BONK    = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WIF     = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
USDC    = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT= "So11111111111111111111111111111111111111112"

CASES = [

    # ── ALTERNATE PHRASINGS — Jupiter price ──────────────────────────────────
    ("JP01", "SOL price",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("JP02", "how much is one solana worth",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("JP03", "give me the USD value of SOL",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("JP04", f"price for mint {SOL_MINT}",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("JP05", "What's JUP trading at right now?",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("JP06", "USDT current price",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("JP07", "prices of SOL, ETH bridged, and USDC on Solana",
     dict(want=("jup_prices","birdeye_token_overview"), has_n=True)),

    # ── ALTERNATE PHRASINGS — swaps ──────────────────────────────────────────
    ("SW01", "if i sell 5 SOL how much USDC do i get",
     dict(want=("jup_quote",), has_n=True)),
    ("SW02", "convert 250 USDC to SOL",
     dict(want=("jup_quote",), has_n=True)),
    ("SW03", "best swap rate for 1 SOL to BONK",
     dict(want=("jup_quote",), has_n=True)),
    ("SW04", "I want to buy BONK with 100 USDC",
     dict(want=("jup_quote",), has_n=True)),
    ("SW05", "quote for swapping exactly 50 SOL to USDC",
     dict(want=("jup_quote","raydium_swap_quote"), has_n=True)),
    ("SW06", "how many WIF tokens can I get for 500 USDC",
     dict(want=("jup_quote",), has_n=True)),
    ("SW07", "swap 1000 JUP to SOL — price and slippage",
     dict(want=("jup_quote",), has_n=True, min_w=15)),
    ("SW08", "what's the price impact of swapping 10000 SOL to USDC",
     dict(want=("jup_quote","raydium_swap_quote"), min_w=15)),

    # ── ALTERNATE PHRASINGS — Solend ─────────────────────────────────────────
    ("SL01", "solend apy for usdc",
     dict(want=("solend_reserves",), kw=("usdc",), has_n=True)),
    ("SL02", "what can i earn by depositing on save finance",
     dict(want=("solend_reserves",), has_n=True, min_w=20)),
    ("SL03", "solend borrow rate for SOL",
     dict(want=("solend_reserves",), has_n=True)),
    ("SL04", "how much collateral does solend require for SOL",
     dict(want=("solend_reserves",), min_w=15)),
    ("SL05", "solend protocol — how healthy is it",
     dict(want=("solend_stats",), min_w=20)),
    ("SL06", "is solend safe? tvl and utilization",
     dict(want=("solend_stats",), has_n=True, min_w=20)),
    ("SL07", "give me the solend snapshot of my positions",
     dict(want=("solend_user_overview","solend_snapshot"), min_w=10)),
    ("SL08", "which wallet has the most borrowed on solend",
     dict(want=("solend_obligations_filtered","solend_squeezy_obligations"), min_w=10)),
    ("SL09", "show solend points for my wallet",
     dict(want=("solend_points",), min_w=10)),

    # ── ALTERNATE PHRASINGS — Kamino ─────────────────────────────────────────
    ("KM01", "kamino interest rates",
     dict(want=("kamino_market_reserves",), has_n=True)),
    ("KM02", "what's the highest yielding asset on kamino",
     dict(want=("kamino_market_reserves","kamino_earn_vaults"), has_n=True, min_w=15)),
    ("KM03", "kamino health factor for my wallet",
     dict(want=("kamino_user_obligations",), min_w=10)),
    ("KM04", "how much can i borrow on kamino with SOL collateral",
     dict(want=("kamino_market_reserves",), min_w=15)),
    ("KM05", "kamino vault yields — which vault is best",
     dict(want=("kamino_earn_vaults",), min_w=20)),
    ("KM06", "show kamino strategy pools with highest APR",
     dict(want=("kamino_strategies",), min_w=15)),

    # ── ALTERNATE PHRASINGS — MarginFi ───────────────────────────────────────
    ("MG01", "marginfi rates",
     dict(want=("marginfi_banks",), has_n=True, min_w=15)),
    ("MG02", "can i borrow USDC on marginfi",
     dict(want=("marginfi_banks",), kw=("usdc",), min_w=15)),
    ("MG03", "marginfi SOL yield",
     dict(want=("marginfi_banks",), has_n=True)),
    ("MG04", "best lend rate on marginfi",
     dict(want=("marginfi_banks",), has_n=True, min_w=10)),

    # ── STAKING — alternate phrasings ────────────────────────────────────────
    ("STK01","what do i earn staking SOL with marinade",
     dict(want=("marinade_msol_apy","marinade_stats"), has_n=True)),
    ("STK02","jitosol vs msol which gives more yield",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","marinade_stats"), has_n=True, min_w=30)),
    ("STK03","msol annual return",
     dict(want=("marinade_msol_apy",), has_n=True)),
    ("STK04","stake sol for passive income — options",
     dict(want=("marinade_stats","jito_stake_pool_stats","marinade_msol_apy"), min_w=30)),
    ("STK05","blazestake bsol apy",
     dict(want=("solend_lst_rates","kamino_staking_yields","marginfi_lst_rates"), has_n=True)),
    ("STK06","liquid staking vs regular staking on solana pros cons",
     dict(no_tool=True, min_w=40)),

    # ── RAYDIUM — alternate phrasings ────────────────────────────────────────
    ("RY01", "raydium top liquidity pools",
     dict(want=("raydium_pools",), min_w=15, has_n=True)),
    ("RY02", "raydium sol usdc pool apr",
     dict(want=("raydium_pool_by_mint","raydium_pools"), has_n=True)),
    ("RY03", "raydium concentrated pools highest fee APR",
     dict(want=("raydium_pools",), has_n=True, min_w=15)),
    ("RY04", "swap 50 SOL for USDC on raydium",
     dict(want=("raydium_swap_quote",), has_n=True)),
    ("RY05", "raydium bonk pool info",
     dict(want=("raydium_pool_by_mint",), kw=("bonk",), min_w=10)),
    ("RY06", "how much TVL does raydium have",
     dict(want=("raydium_info",), has_n=True)),
    ("RY07", "raydium recommended transaction fee right now",
     dict(want=("raydium_auto_fee","jito_tip_floor"), has_n=True)),

    # ── ORCA — alternate phrasings ────────────────────────────────────────────
    ("OR01", "orca whirlpool best pools",
     dict(want=("orca_pools",), min_w=15)),
    ("OR02", "how much volume does orca do daily",
     dict(want=("orca_protocol_stats",), has_n=True)),
    ("OR03", "orca sol-usdc liquidity",
     dict(want=("orca_pools_search","orca_pools"), min_w=10)),
    ("OR04", "orca token price",
     dict(want=("orca_protocol_token","jup_prices","birdeye_token_overview"), has_n=True)),

    # ── BIRDEYE — alternate phrasings ────────────────────────────────────────
    ("BD01", "bonk 24h price change",
     dict(want=("birdeye_price_stats","birdeye_token_overview","jup_search"), has_n=True)),
    ("BD02", "sol trading volume last 24 hours",
     dict(want=("birdeye_price_volume","birdeye_pair_overview","dex_search"), has_n=True)),
    ("BD03", "top gainers on solana today",
     dict(want=("birdeye_token_list","birdeye_token_trending","jup_trending"), min_w=15)),
    ("BD04", "most traded solana tokens this week",
     dict(want=("birdeye_token_list","jup_trending","birdeye_token_trending"), min_w=15)),
    ("BD05", "check if bonk has been rug pulled — security audit",
     dict(want=("birdeye_token_security","token_security","jup_search"), kw=("bonk",), min_w=20)),
    ("BD06", f"full portfolio analysis for {WALLET}",
     dict(want=("birdeye_wallet_current_net_worth","birdeye_wallet_pnl_summary"), min_w=20)),
    ("BD07", f"did {WALLET} make money trading",
     dict(want=("birdeye_wallet_pnl_summary","birdeye_wallet_pnl_details"), min_w=15)),
    ("BD08", "recent on-chain trades for SOL token",
     dict(want=("birdeye_token_txs","birdeye_txs_recent","birdeye_pair_txs"), min_w=10)),
    ("BD09", "sol ohlcv candle data last hour",
     dict(want=("birdeye_ohlcv","birdeye_ohlcv_v1"), has_n=True)),
    ("BD10", "top holders of JUP token",
     dict(want=("helius_token_holders","birdeye_token_holders","birdeye_holder_distribution","birdeye_holder_profile"), min_w=10)),
    ("BD11", "wif token metadata — description, website, twitter",
     dict(want=("birdeye_token_metadata","jup_search"), kw=("wif",), min_w=10)),
    ("BD12", f"token breakdown in wallet {WALLET2}",
     dict(want=("birdeye_wallet_token_list","helius_wallet_tokens","birdeye_wallet_current_net_worth"), min_w=10)),

    # ── DEXSCREENER — alternate phrasings ────────────────────────────────────
    ("DS01", "dexscreener trending pairs solana",
     dict(want=("dex_trending","birdeye_token_trending","jup_trending"), min_w=15)),
    ("DS02", "find all trading pairs for BONK",
     dict(want=("dex_token","dex_search","birdeye_pair_overview"), kw=("bonk",), min_w=10)),
    ("DS03", "wif token dex pairs and liquidity",
     dict(want=("dex_token","dex_search","birdeye_token_overview"), kw=("wif",), min_w=10)),

    # ── CROSS-PROTOCOL COMPLEX SCENARIOS ─────────────────────────────────────
    ("XP01", "I want to maximize yield on 5000 USDC. Compare lending, LP, and vaults.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","raydium_pools","kamino_earn_vaults"), has_n=True, min_w=80)),
    ("XP02", "Should I put my SOL in jitoSOL, mSOL, or just lend on Kamino?",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","kamino_market_reserves"), has_n=True, min_w=50)),
    ("XP03", "I'm bearish on SOL — what DeFi strategies make sense?",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks"), min_w=40)),
    ("XP04", "best risk-adjusted yield on Solana for stablecoins",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","raydium_pools","kamino_earn_vaults"), has_n=True, min_w=50)),
    ("XP05", "what is the total SOL yield available across all protocols",
     dict(want=("solend_reserves","kamino_market_reserves","jito_stake_pool_stats","marinade_stats"), has_n=True, min_w=40)),
    ("XP06", "give me a Solana DeFi dashboard — TVL, yields, volumes across all major protocols",
     dict(want=("solend_stats","kamino_markets","raydium_info","marinade_stats"), min_w=100)),
    ("XP07", "how do Raydium and Orca compare for SOL-USDC liquidity provision",
     dict(want=("raydium_pool_by_mint","raydium_pools","orca_pools_search","orca_pools"), min_w=40)),
    ("XP08", "which protocol has the lowest borrowing cost for SOL",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks"), has_n=True, min_w=30)),
    ("XP09", "I want to lever long SOL — which protocol is cheapest to borrow from",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks"), has_n=True, min_w=30)),

    # ── WALLET — multiple wallets / adversarial ───────────────────────────────
    ("WA01", f"solend position for {WALLET2}",
     dict(want=("solend_user_overview",), min_w=10)),
    ("WA02", "check wallet 11111111111111111111111111111111 on solend",
     dict(want=("solend_user_overview",), min_w=5)),
    ("WA03", f"kamino positions for {WALLET2}",
     dict(want=("kamino_user_obligations",), min_w=10)),
    ("WA04", f"net worth of {WALLET2}",
     dict(want=("birdeye_wallet_current_net_worth",), has_n=True)),
    ("WA05", "check wallet INVALIDWALLETXYZ for all defi positions",
     dict(min_w=5)),

    # ── JITO — alternate phrasings ────────────────────────────────────────────
    ("JI01", "current mev tip on solana",
     dict(want=("jito_tip_floor",), min_w=5)),
    ("JI02", "jito validator mev earnings",
     dict(want=("jito_mev_rewards","jito_daily_mev"), min_w=10)),
    ("JI03", "how much sol is staked with jito",
     dict(want=("jito_stake_pool_stats",), has_n=True)),
    ("JI04", "jitosol vs sol yield differential",
     dict(want=("jito_stake_pool_stats","jito_jitosol_ratio"), has_n=True, min_w=20)),

    # ── MARINADE — alternate phrasings ───────────────────────────────────────
    ("MN01", "marinade tvl",
     dict(want=("marinade_stats",), has_n=True)),
    ("MN02", "msol price in sol",
     dict(want=("marinade_stats",), has_n=True)),
    ("MN03", "marinade 7 day apy",
     dict(want=("marinade_msol_apy",), has_n=True)),
    ("MN04", "marinade 1 year historical apy",
     dict(want=("marinade_msol_apy",), has_n=True)),
    ("MN05", "which validators does marinade delegate to",
     dict(want=("marinade_validators",), min_w=10)),
    ("MN06", "marinade governance — how does voting work",
     dict(want=("marinade_msol_votes","marinade_vemnde_votes"), min_w=10)),

    # ── TOKEN DISCOVERY ───────────────────────────────────────────────────────
    ("TD01", "find tokens launched in last 24 hours on solana",
     dict(want=("jup_recent","birdeye_new_listings"), min_w=15)),
    ("TD02", "show me pump.fun tokens that went viral",
     dict(want=("jup_recent","jup_trending","birdeye_new_listings"), min_w=15)),
    ("TD03", "top organic demand tokens on solana",
     dict(want=("jup_trending",), min_w=10)),
    ("TD04", "solana meme coins with highest volume",
     dict(want=("jup_trending","birdeye_token_list","dex_trending"), min_w=15)),
    ("TD05", f"find the mint address of BONK",
     dict(want=("jup_search",), kw=("bonk",))),
    ("TD06", f"what is the contract address of JUP token",
     dict(want=("jup_search",))),
    ("TD07", "show me all verified tokens on Jupiter",
     dict(want=("jup_tokens_tag",), min_w=10)),
    ("TD08", "is this a scam token? EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
     dict(want=("jup_search","birdeye_token_security","token_security"), min_w=15)),

    # ── MEV / FEES / NETWORK ──────────────────────────────────────────────────
    ("NW01", "what priority fee should i use for my solana tx",
     dict(want=("jito_tip_floor","raydium_auto_fee"), has_n=True)),
    ("NW02", "solana network congestion — how are fees right now",
     dict(want=("jito_tip_floor","raydium_auto_fee"), min_w=10)),
    ("NW03", "jito bundle tip floor today",
     dict(want=("jito_tip_floor",), has_n=True)),

    # ── ADVERSARIAL / AMBIGUOUS INPUTS ───────────────────────────────────────
    ("AD01", "apy",
     dict(min_w=5)),
    ("AD02", "price",
     dict(min_w=5)),
    ("AD03", "show me everything",
     dict(min_w=10)),
    ("AD04", "best",
     dict(min_w=5)),
    ("AD05", "is this good or bad",
     dict(min_w=5)),
    ("AD06", "tell me about defi",
     dict(no_tool=True, min_w=20)),
    ("AD07", "what should i do with my money",
     dict(min_w=10)),
    ("AD08", "solana",
     dict(min_w=5)),
    ("AD09", "????",
     dict(min_w=3)),
    ("AD10", "1000000000 USDC to SOL swap",
     dict(want=("jup_quote",), has_n=True)),

    # ── TURKISH LANGUAGE QUERIES (user base is Turkish) ───────────────────────
    ("TR01", "SOL fiyatı nedir",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("TR02", "Solend'de USDC yatırırsam ne kazanırım",
     dict(want=("solend_reserves",), has_n=True, min_w=15)),
    ("TR03", "en iyi getiri nerede",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks"), min_w=20)),
    ("TR04", "Raydium'da SOL-USDC havuzu APR",
     dict(want=("raydium_pool_by_mint","raydium_pools"), has_n=True)),
    ("TR05", "Kamino'da faiz oranları",
     dict(want=("kamino_market_reserves",), has_n=True, min_w=10)),
    ("TR06", "500 USDC ile kaç SOL alırım",
     dict(want=("jup_quote",), has_n=True)),
    ("TR07", "Jito tip ücreti şu an kaç",
     dict(want=("jito_tip_floor",), has_n=True)),
    ("TR08", "mSOL APY ne kadar",
     dict(want=("marinade_msol_apy",), has_n=True)),
    ("TR09", "jitoSOL mı mSOL mu daha iyi",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","marinade_stats"), min_w=30)),
    ("TR10", "BONK güvenli mi",
     dict(want=("jup_search","birdeye_token_security","token_security"), kw=("bonk",), min_w=15)),
    ("TR11", "cüzdanımdaki tokenlar neler",
     dict(min_w=5)),
    ("TR12", "Solana'da en çok işlem gören tokenlar",
     dict(want=("jup_trending","birdeye_token_list","dex_trending"), min_w=15)),
    ("TR13", "Marinade ile stake etmenin avantajları neler",
     dict(want=("marinade_stats","marinade_msol_apy"), min_w=20)),
    ("TR14", "Orca havuzlarında en iyi APR hangisi",
     dict(want=("orca_pools",), has_n=True, min_w=15)),
    ("TR15", "DeFi lending nedir nasıl çalışır",
     dict(no_tool=True, min_w=40)),

    # ── GENERAL KNOWLEDGE (must NOT call tools) ───────────────────────────────
    ("GK01", "explain automated market makers",
     dict(no_tool=True, min_w=30)),
    ("GK02", "what is a liquidity provider",
     dict(no_tool=True, min_w=20)),
    ("GK03", "how does yield farming work",
     dict(no_tool=True, min_w=30)),
    ("GK04", "what are the risks of DeFi",
     dict(no_tool=True, min_w=30)),
    ("GK05", "explain flash loans",
     dict(no_tool=True, min_w=25)),
    ("GK06", "what is MEV on solana",
     dict(no_tool=True, min_w=20)),
    ("GK07", "difference between AMM and order book",
     dict(no_tool=True, min_w=25)),
    ("GK08", "what is a health factor in lending",
     dict(no_tool=True, min_w=20)),
    ("GK09", "how does Jito MEV protection work",
     dict(no_tool=True, min_w=20)),
    ("GK10", "what is the difference between mSOL and jitoSOL",
     dict(no_tool=True, min_w=30)),

    # ── COMPLEX NARRATIVE QUERIES ─────────────────────────────────────────────
    ("NR01", "I'm new to Solana DeFi. Where should I start to earn yield safely?",
     dict(want=("solend_reserves","kamino_market_reserves","marinade_stats","jito_stake_pool_stats"), min_w=60)),
    ("NR02", "I have a SOL heavy portfolio. How do I hedge my risk while earning yield?",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks"), min_w=50)),
    ("NR03", "Analyze BONK as an investment — price action, holders, safety, volume",
     dict(want=("jup_prices","jup_search","birdeye_token_overview","birdeye_token_security"), kw=("bonk",), min_w=60)),
    ("NR04", "Give me a morning briefing on Solana DeFi — protocol health, yields, market conditions",
     dict(want=("solend_stats","kamino_markets","jito_tip_floor","marinade_stats"), min_w=80)),
    ("NR05", "I deposited SOL on Solend last week. How is the protocol performing?",
     dict(want=("solend_stats","solend_reserves","solend_daily_stats"), min_w=40)),
    ("NR06", "Which lending protocol has the best combination of yield AND safety?",
     dict(want=("solend_reserves","solend_stats","kamino_market_reserves","marginfi_banks"), min_w=50)),
    ("NR07", "I want to farm BONK rewards — what are my options on Solana?",
     dict(want=("jup_prices","birdeye_token_overview","raydium_pool_by_mint","dex_search"), kw=("bonk",), min_w=40)),
    ("NR08", "Is now a good time to provide liquidity on Raydium?",
     dict(want=("raydium_pools","raydium_info"), min_w=30)),

    # ── PROTOCOL-SPECIFIC EDGE CASES ─────────────────────────────────────────
    ("PE01", "solend daily stats for 2025-01-01",
     dict(want=("solend_daily_stats",), min_w=5)),
    ("PE02", "raydium pool for a non-existent token pair: FAKEMINT1 and FAKEMINT2",
     dict(want=("raydium_pool_by_mint",), min_w=5)),
    ("PE03", "kamino reserves for an unknown market",
     dict(want=("kamino_market_reserves","kamino_markets"), min_w=5)),
    ("PE04", "solend positions for a wallet with no activity: 11111111111111111111111111111111",
     dict(want=("solend_user_overview",), min_w=5)),
    ("PE05", "show solend airdrop for my wallet — i have no wallet",
     dict(min_w=5)),
    ("PE06", "jito tip floor at midnight yesterday",
     dict(want=("jito_tip_floor",), min_w=5)),
    ("PE07", "marinade apy for the next 5 years",
     dict(want=("marinade_msol_apy",), min_w=5)),
    ("PE08", "swap -100 USDC to SOL",
     dict(min_w=5)),
    ("PE09", "0 SOL to USDC price",
     dict(min_w=5)),
    ("PE10", "what is the APY for a token that doesnt exist: XYZABC123FAKE",
     dict(min_w=5)),

    # ── REPEAT INTENT DIFFERENT WORDS (routing consistency) ──────────────────
    ("RI01", "solend tvl",
     dict(want=("solend_stats",), has_n=True)),
    ("RI02", "save finance total deposits",
     dict(want=("solend_stats",), has_n=True)),
    ("RI03", "how much money is locked in solend",
     dict(want=("solend_stats",), has_n=True)),
    ("RI04", "kamino total value locked",
     dict(want=("kamino_markets","kamino_market_reserves"), has_n=True)),
    ("RI05", "raydium dex volume",
     dict(want=("raydium_info",), has_n=True)),
    ("RI06", "orca dex tvl",
     dict(want=("orca_protocol_stats",), has_n=True)),
    ("RI07", "jup token price",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("RI08", "bonk price usd",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("RI09", "how much is wif worth",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_n=True)),
    ("RI10", "sol usdc swap jupiter",
     dict(want=("jup_quote",), has_n=True)),
]

# ═══════════════════════════════════════════════════════════════════════════

async def main():
    print(f"\n{BOLD}{'═'*72}")
    print(f"  STRESS TEST ROUND 2 — {len(CASES)} cases — {CONCURRENCY} concurrent")
    print(f"{'═'*72}{E}\n")

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            h = await c.get(f"{BASE}/health")
            print(f"  Service: {h.json()}\n")
    except Exception as e:
        print(f"{R}  Unreachable: {e}{E}"); sys.exit(1)

    sem = asyncio.Semaphore(CONCURRENCY)
    t0  = time.time()

    await asyncio.gather(*[run(tc, q, sem, **kw) for tc, q, kw in CASES])

    elapsed = time.time() - t0
    total   = stats["pass"] + stats["fail"]
    pct     = int(stats["pass"] / total * 100) if total else 0

    print(f"\n{BOLD}{'═'*72}")
    print(f"  RESULT: {G}{stats['pass']} PASS{E}  {R}{stats['fail']} FAIL{E}  "
          f"— {BOLD}{pct}%{E}  ({elapsed/60:.1f} min)")
    print(f"{'═'*72}{E}")

    if fails:
        print(f"\n{R}{BOLD}  FAILURES ({len(fails)}):{E}")
        for f in sorted(fails):
            print(f"  {R}• {f}{E}")
    print()

if __name__ == "__main__":
    asyncio.run(main())

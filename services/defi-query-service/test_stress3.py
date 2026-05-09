"""
Stress test round 3 — multi-protocol simultaneous pulls, deep analysis,
model limit stress, synthesis queries, max tool-call chains.
Parallel execution (4 concurrent — heavier queries need more headroom).
"""
import asyncio, httpx, time, sys

BASE        = "http://localhost:3150"
TIMEOUT     = 150   # longer — multi-tool chains take more time
CONCURRENCY = 4

R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
W="\033[97m"; D="\033[90m"; E="\033[0m";  BOLD="\033[1m"

stats = {"pass": 0, "fail": 0}
fails = []
lock  = asyncio.Lock()

async def ask(q: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                    r = await c.post(f"{BASE}/query", json={"question": q})
                    if r.status_code in (429, 500) and attempt < 2:
                        await asyncio.sleep(30 + attempt * 30)
                        continue
                    r.raise_for_status()
                    return r.json()
            except Exception as ex:
                if attempt < 2: await asyncio.sleep(30)
                else: return {"__err": str(ex), "plain": "", "tools_called": []}
        return {"__err": "max_retries", "plain": "", "tools_called": []}

def hit(called, *want):     return bool(set(called) & set(want))
def miss_kw(plain, *kw):    lo = plain.lower(); return [w for w in kw if w.lower() not in lo]
def wc(plain):              return len(plain.split())
def has_num(plain):         return any(c.isdigit() for c in plain)
def raw_err(plain):
    bads = ["internal server error","traceback","exception:","temporarily unavailable","attributeerror"]
    lo = plain.lower(); return [b for b in bads if b in lo]
def tool_count(called, *names): return sum(1 for t in called if t in names)

async def run(tc, q, sem, want=(), kw=(), min_w=0, no_tool=False, has_n=False,
              min_tools=0, want_all=()):
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
        if want_all:
            missing_tools = [t for t in want_all if t not in called]
            if missing_tools:
                errs.append(f"missing_required_tools={missing_tools} called={called}")
        if min_tools and len(set(called)) < min_tools:
            errs.append(f"too_few_distinct_tools={len(set(called))}<{min_tools} called={called}")
        if kw:
            m = miss_kw(plain, *kw)
            if m: errs.append(f"missing_kw={m}")
        if min_w and wc(plain) < min_w:
            errs.append(f"too_short={wc(plain)}<{min_w}")
        if has_n and not has_num(plain):
            errs.append("no_number_in_response")
        re = raw_err(plain)
        if re: errs.append(f"raw_error={re}")

    async with lock:
        if not errs:
            stats["pass"] += 1
            print(f"{G}✓{E} [{tc}] tools={called}")
        else:
            stats["fail"] += 1
            detail = " | ".join(errs)
            fails.append(f"[{tc}] {q[:65]!r}  →  {detail}")
            print(f"{R}✗{E} [{tc}] {detail[:120]}")

# ═══════════════════════════════════════════════════════════════════════════
WALLET = "CakcnaRDHka2gXyfbEd2d3xsvkJkqsLw2akB3zsN1D2S"

CASES = [

    # ══ SIMULTANEOUS MULTI-PROTOCOL PULLS ═══════════════════════════════════
    # Pull data from 4+ protocols in a single query

    ("MP01", "Pull SOL lending rates from Solend, Kamino, and MarginFi simultaneously, then rank them best to worst.",
     dict(want_all=("solend_reserves","kamino_market_reserves","marginfi_banks"),
          min_tools=3, has_n=True, min_w=60)),

    ("MP02", "Fetch USDC supply APY from Solend, Kamino, and MarginFi at the same time and show a comparison table.",
     dict(want_all=("solend_reserves","kamino_market_reserves","marginfi_banks"),
          min_tools=3, has_n=True, min_w=60)),

    ("MP03", "Get Jito tip floor, Raydium recommended fee, and Solana epoch info all at once.",
     dict(want_all=("jito_tip_floor","raydium_auto_fee","kamino_epoch_info"),
          min_tools=3, has_n=True, min_w=30)),

    ("MP04", "Simultaneously show: Solend TVL, Kamino TVL, Raydium TVL, Marinade TVL, Orca TVL.",
     dict(want=("solend_stats","kamino_markets","raydium_info","marinade_stats","orca_protocol_stats"),
          min_tools=4, has_n=True, min_w=50)),

    ("MP05", "At the same time, pull: Jito MEV rewards, Marinade epoch rewards, and Raydium pool volume.",
     dict(want=("jito_mev_rewards","marinade_epoch_rewards","raydium_pools","raydium_info"),
          min_tools=3, min_w=40)),

    ("MP06", "Fetch SOL price from Jupiter, BONK price, WIF price, and JUP price all together.",
     dict(want=("jup_prices","birdeye_token_overview"), min_tools=1, has_n=True, min_w=30)),

    ("MP07", "Pull Orca protocol stats, Meteora pools, and Raydium info simultaneously to compare DEX performance.",
     dict(want=("orca_protocol_stats","meteora_pools","raydium_info"),
          min_tools=3, min_w=40)),

    ("MP08", "Get jitoSOL APY, mSOL APY, and bSOL APY from all sources at once.",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","solend_lst_rates","kamino_staking_yields"),
          min_tools=3, has_n=True, min_w=40)),

    ("MP09", "Simultaneously: Solend daily stats, Solend daily fees, Solend liquidations, Solend stats.",
     dict(want_all=("solend_daily_stats","solend_daily_fees","solend_stats"),
          min_tools=3, has_n=True, min_w=40)),

    ("MP10", "Pull BONK data from Jupiter (price, search), Birdeye (overview, security), and DexScreener (pairs) all at once.",
     dict(want=("jup_prices","jup_search","birdeye_token_overview","birdeye_token_security","dex_token"),
          min_tools=3, kw=("bonk",), min_w=50)),

    # ══ DEEP ANALYSIS — MODEL SYNTHESIS ═════════════════════════════════════

    ("DA01", "Comprehensive Solana DeFi yield landscape analysis: lending rates (Solend/Kamino/MarginFi), LST staking (jitoSOL/mSOL), LP pools (Raydium/Orca top pools). Rank all yield sources from highest to lowest APY.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","jito_stake_pool_stats","marinade_msol_apy","raydium_pools","orca_pools"),
          min_tools=5, has_n=True, min_w=150)),

    ("DA02", "Full protocol health report for the Solana ecosystem: check Solend TVL+utilization, Kamino markets, Raydium volume, Marinade validators, Orca protocol stats. Flag any concerns.",
     dict(want=("solend_stats","kamino_markets","raydium_info","marinade_stats","orca_protocol_stats"),
          min_tools=4, has_n=True, min_w=120)),

    ("DA03", "Deep dive into BONK token: current price and 24h change, market cap, top holders, security audit, DEX trading pairs, on-chain volume. Give a full investment analysis.",
     dict(want=("jup_prices","birdeye_token_overview","birdeye_token_security","dex_token","jup_search"),
          min_tools=3, kw=("bonk",), has_n=True, min_w=150)),

    ("DA04", f"Complete DeFi portfolio audit for wallet {WALLET}: Solend positions, Kamino positions, token balances, net worth, PnL. Flag any liquidation risks.",
     dict(want=("solend_user_overview","kamino_user_obligations","birdeye_wallet_current_net_worth","birdeye_wallet_pnl_summary"),
          min_tools=3, min_w=80)),

    ("DA05", "Market microstructure analysis for SOL/USDC: check Raydium pool liquidity+APR, Orca pool liquidity, Jupiter swap quote for 10000 USDC, and Birdeye 24h volume. Which venue has the best execution?",
     dict(want=("raydium_pool_by_mint","orca_pools_search","jup_quote","birdeye_price_volume"),
          min_tools=3, has_n=True, min_w=100)),

    ("DA06", "Analyze Marinade staking in depth: TVL and mSOL price, 7d/30d/1y APY history, validator quality scores, epoch rewards, upcoming delegation plan.",
     dict(want=("marinade_stats","marinade_msol_apy","marinade_validator_scores","marinade_epoch_rewards","marinade_staking_report"),
          min_tools=4, has_n=True, min_w=120)),

    ("DA07", "Full Raydium ecosystem report: protocol TVL and volume, top 10 pools by APR, top pools by TVL, CLMM vs AMM breakdown, swap fee comparison, auto-fee recommendation.",
     dict(want=("raydium_info","raydium_pools","raydium_auto_fee"),
          min_tools=2, has_n=True, min_w=120)),

    ("DA08", "Jito MEV ecosystem deep analysis: current tip floor, historical MEV rewards, jitoSOL APY, jitoSOL/SOL ratio history, total staked with Jito.",
     dict(want=("jito_tip_floor","jito_mev_rewards","jito_stake_pool_stats","jito_jitosol_ratio"),
          min_tools=3, has_n=True, min_w=100)),

    ("DA09", "Kamino full product suite analysis: lending markets (reserves, APYs, utilization), Earn vaults (best yield), LP strategies, LST staking yields, leverage stats.",
     dict(want=("kamino_market_reserves","kamino_earn_vaults","kamino_strategies","kamino_staking_yields","kamino_leverage_stats"),
          min_tools=4, has_n=True, min_w=120)),

    ("DA10", "Cross-protocol LST analysis: jitoSOL APY (Jito), mSOL APY (Marinade 7d+30d+1y), bSOL (BlazeStake via Solend), LST rates on Kamino and MarginFi. Build a comprehensive comparison table.",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","solend_lst_rates","kamino_staking_yields","marginfi_lst_rates"),
          min_tools=4, has_n=True, min_w=120)),

    # ══ LIMIT STRESS — max tool chains, large responses ══════════════════════

    ("LS01", "Pull ALL Solend data you can: markets, reserves, stats, daily stats, daily fees, LST rates, reward stats, isolated pools, points, referral stats, liquidations, circulating supply.",
     dict(want=("solend_markets","solend_reserves","solend_stats","solend_daily_fees","solend_reward_stats"),
          min_tools=5, has_n=True, min_w=150)),

    ("LS02", "Pull ALL Raydium data: protocol info, top pools (all types), SOL-USDC pool specifically, CLMM config, CPMM config, auto fee, stake pools.",
     dict(want=("raydium_info","raydium_pools","raydium_pool_by_mint","raydium_auto_fee"),
          min_tools=4, has_n=True, min_w=100)),

    ("LS03", "Get everything about BONK: price, 24h change, market cap, volume, top holders, security, DEX pairs, recent transactions, mint/burn history.",
     dict(want=("jup_prices","birdeye_token_overview","dex_token","jup_search"),
          min_tools=3, kw=("bonk",), has_n=True, min_w=150)),

    ("LS04", "Full Marinade data pull: stats, mSOL APY (all periods 7d/30d/1y/2y), validators, validator scores, epoch rewards, native APY, staking report, Jito commissions, cluster stats.",
     dict(want=("marinade_stats","marinade_msol_apy","marinade_validators","marinade_epoch_rewards","marinade_native_apy"),
          min_tools=4, has_n=True, min_w=120)),

    ("LS05", "Comprehensive Orca analysis: protocol stats, top pools, ORCA token price and market cap, circulating supply, total supply, token list.",
     dict(want=("orca_protocol_stats","orca_pools","orca_protocol_token","orca_circulating_supply"),
          min_tools=3, has_n=True, min_w=80)),

    ("LS06", "Show me every lending protocol on Solana and their key metrics simultaneously: Solend, Kamino, MarginFi — TVL, top assets, APYs, utilization rates.",
     dict(want_all=("solend_stats","kamino_markets","marginfi_banks"),
          min_tools=3, has_n=True, min_w=150)),

    # ══ FINANCIAL MODELING & OPTIMIZATION ════════════════════════════════════

    ("FM01", "I have $50,000 to deploy on Solana DeFi. Optimize yield: split between best stablecoin lending, LST staking, and SOL lending. Show exact APYs and recommended allocation.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","jito_stake_pool_stats","marinade_msol_apy"),
          min_tools=4, has_n=True, min_w=120)),

    ("FM02", "Calculate: if I lend 100 SOL on the best protocol for 1 year, how much would I earn? Show me the top 3 options with projected earnings.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks"),
          has_n=True, min_w=80)),

    ("FM03", "I'm a liquidity provider. Compare: Raydium SOL-USDC CLMM pool vs Orca SOL-USDC pool — fee APR, TVL depth, IL risk, which is better?",
     dict(want=("raydium_pool_by_mint","raydium_pools","orca_pools_search","orca_pools"),
          min_tools=2, min_w=80)),

    ("FM04", "Yield strategy: stack rewards by lending jitoSOL on Solend or Kamino WHILE earning jitoSOL staking APY. What's the combined yield?",
     dict(want=("solend_reserves","kamino_market_reserves","jito_stake_pool_stats","solend_lst_rates"),
          has_n=True, min_w=80)),

    ("FM05", "Compare dollar cost averaging into SOL vs putting that money into jitoSOL staking. Use current APY data.",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","jup_prices"),
          has_n=True, min_w=60)),

    # ══ ADVERSARIAL — confusing, contradictory, overloaded ═══════════════════

    ("AV01", "Show me Solend APY, Kamino APY, MarginFi APY, Raydium pool APR, Orca pool APR, jitoSOL APY, mSOL APY, and then rank all of them and tell me the best one.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","raydium_pools","jito_stake_pool_stats"),
          min_tools=4, has_n=True, min_w=120)),

    ("AV02", "Give me live data on: SOL price, BONK price, WIF price, JUP price, ORCA price, RAY price — all at once.",
     dict(want=("jup_prices","birdeye_token_overview"), has_n=True, min_w=30)),

    ("AV03", "Compare Solend AND Kamino AND MarginFi AND Raydium AND Orca AND Marinade AND Jito — summarize each protocol's TVL, key product, and top yield.",
     dict(want=("solend_stats","kamino_markets","raydium_info","marinade_stats","orca_protocol_stats","jito_stake_pool_stats"),
          min_tools=5, has_n=True, min_w=150)),

    ("AV04", "I want to borrow SOL to buy more SOL. And also stake that SOL for jitoSOL. And then lend jitoSOL. And see what the Raydium jitoSOL pool is paying. Analyze all of it.",
     dict(want=("solend_reserves","kamino_market_reserves","jito_stake_pool_stats","raydium_pool_by_mint","solend_lst_rates"),
          min_tools=3, has_n=True, min_w=80)),

    ("AV05", "What if I: swap 10000 USDC to SOL on Jupiter, stake it as jitoSOL, then lend jitoSOL on Kamino, and borrow USDC against it to swap back to SOL again. What are all the rates?",
     dict(want=("jup_quote","jito_stake_pool_stats","kamino_market_reserves","solend_lst_rates"),
          min_tools=3, has_n=True, min_w=80)),

    ("AV06", "Show me everything happening on Solana DeFi right now simultaneously: prices, yields, pools, MEV, staking, new tokens.",
     dict(want=("jup_prices","solend_stats","raydium_pools","jito_tip_floor","jup_trending"),
          min_tools=4, min_w=100)),

    # ══ CROSS-PROTOCOL ARBITRAGE & STRATEGY ══════════════════════════════════

    ("CS01", "Is there a lending rate arbitrage between Solend and Kamino for SOL? Check both borrow and supply rates.",
     dict(want_all=("solend_reserves","kamino_market_reserves"),
          has_n=True, min_w=60)),

    ("CS02", "Check if there's a yield gap between mSOL APY and lending mSOL on Solend/Kamino. Is it worth lending your mSOL?",
     dict(want=("marinade_msol_apy","solend_lst_rates","kamino_staking_yields","kamino_market_reserves"),
          has_n=True, min_w=60)),

    ("CS03", "Compare SOL-USDC swap price: get Jupiter quote vs Raydium quote for 1000 USDC. Which gives more SOL?",
     dict(want=("jup_quote","raydium_swap_quote"), has_n=True, min_w=40)),

    ("CS04", "Jito tip floor is the cost of priority. Raydium auto-fee is another estimate. Compare both and recommend which to use for a time-sensitive swap.",
     dict(want_all=("jito_tip_floor","raydium_auto_fee"), has_n=True, min_w=40)),

    ("CS05", "Check Solend reserve APY history vs current APY for SOL — has the rate gone up or down?",
     dict(want=("solend_reserves","solend_reserves_history"), min_w=30)),

    # ══ WALLET DEEP DIVES ═════════════════════════════════════════════════════

    ("WD01", f"Full risk audit for wallet {WALLET}: check Solend health factor, Kamino health factor, any positions near liquidation, net worth.",
     dict(want=("solend_user_overview","kamino_user_obligations","birdeye_wallet_current_net_worth"),
          min_tools=2, min_w=40)),

    ("WD02", f"Earnings report for {WALLET}: Solend claimable rewards, PnL from trading, net worth over time.",
     dict(want=("solend_confirmed_rewards","birdeye_wallet_pnl_summary","birdeye_wallet_current_net_worth"),
          min_tools=2, min_w=40)),

    ("WD03", f"Token analysis for wallet {WALLET}: what tokens does it hold, their current prices and values, total portfolio value.",
     dict(want=("birdeye_wallet_token_list","birdeye_wallet_current_net_worth","jup_prices"),
          min_tools=2, has_n=True, min_w=40)),

    # ══ REAL-TIME MARKET STRESS ═══════════════════════════════════════════════

    ("RT01", "In the last 5 minutes: what are the most active trading pairs on Solana? Use birdeye and dex data.",
     dict(want=("birdeye_token_trending","dex_trending","birdeye_txs_recent","birdeye_token_txs"),
          min_tools=2, min_w=20)),

    ("RT02", "Real-time Solana DeFi pulse: SOL price, Jito tip, top trending token, best lending yield — all right now.",
     dict(want=("jup_prices","jito_tip_floor","jup_trending","solend_reserves"),
          min_tools=3, has_n=True, min_w=50)),

    ("RT03", "Pull live Raydium pool data and find the pool with best risk-adjusted return (fee APR / TVL ratio).",
     dict(want=("raydium_pools",), has_n=True, min_w=40)),

    ("RT04", "What is the current cost of executing a high-priority Solana transaction? Check Jito tip floor and any other fee estimates.",
     dict(want=("jito_tip_floor","raydium_auto_fee"), has_n=True, min_w=20)),

    # ══ TOKEN COMPARISON STRESS ═══════════════════════════════════════════════

    ("TC01", "Compare BONK vs WIF vs JUP vs ORCA vs RAY — price, market cap, 24h volume, 24h change. Build a leaderboard.",
     dict(want=("jup_prices","birdeye_token_overview","birdeye_token_market_data_multi"),
          has_n=True, min_w=80)),

    ("TC02", "Show me the 5 safest tokens on Solana based on audit scores and organic demand.",
     dict(want=("jup_trending","birdeye_token_list","jup_search"), min_w=40)),

    ("TC03", "Which has more on-chain activity: BONK or WIF? Compare transactions, volume, unique traders.",
     dict(want=("birdeye_token_overview","birdeye_price_volume","dex_token","jup_search"),
          kw=("bonk","wif"), min_w=40)),

    ("TC04", "Compare the top 3 meme coins on Solana right now — market cap, liquidity, holder count, safety.",
     dict(want=("jup_trending","birdeye_token_list","jup_search","birdeye_token_overview"),
          min_tools=2, min_w=60)),

    # ══ PROTOCOL STRESS — obscure queries ════════════════════════════════════

    ("PS01", "Show me Solend reserve config changes — what protocol parameters changed recently?",
     dict(want=("solend_reserves_config_changes",), min_w=10)),

    ("PS02", "Who are the top borrowers on Solend right now? Show obligations filtered by large borrows.",
     dict(want=("solend_obligations_filtered",), min_w=10)),

    ("PS03", "Show Solend squeezy obligations for USDC — positions most at risk of liquidation.",
     dict(want=("solend_squeezy_obligations",), min_w=10)),

    ("PS04", "What is Marinade system config — program addresses, delegation authorities?",
     dict(want=("marinade_config",), min_w=10)),

    ("PS05", "Show Marinade scoring methodology and past scoring reports.",
     dict(want=("marinade_scoring_reports",), min_w=10)),

    ("PS06", "Show Marinade validator uptime history for a top validator.",
     dict(want=("marinade_validator_uptimes","marinade_validators"), min_w=10)),

    ("PS07", "What are Marinade Jito MEV commissions for validators?",
     dict(want=("marinade_jito_commissions",), min_w=10)),

    ("PS08", "Show Orca locked liquidity in the SOL-USDC pool.",
     dict(want=("orca_locked_liquidity","orca_pools_search"), min_w=5)),

    ("PS09", "What tokens can I trade on Orca? Search for BONK on Orca.",
     dict(want=("orca_tokens_search","orca_tokens"), kw=("bonk",), min_w=10)),

    ("PS10", "Kamino leverage stats — what is the average leverage ratio across all positions?",
     dict(want=("kamino_leverage_stats",), has_n=True, min_w=10)),

    ("PS11", "Show Solend SAVE token price chart for last 30 days.",
     dict(want=("solend_save_price_chart",), min_w=5)),

    ("PS12", "What is Solend Save protocol revenue over the last 90 days?",
     dict(want=("solend_save_revenue_chart",), min_w=5)),

    ("PS13", "Show Solend VIP eligibility criteria and check a wallet.",
     dict(want=("solend_vip_eligibility",), min_w=10)),

    ("PS14", "What is the Solend referral program — how much do referrers earn?",
     dict(want=("solend_referral_stats","solend_referral_payments"), min_w=15)),

    ("PS15", "Show Raydium IDO / launchpad programs.",
     dict(want=("raydium_ido_keys",), min_w=5)),

    # ══ LONG-FORM SYNTHESIS — highest complexity ══════════════════════════════

    ("LF01", "Write a complete Solana DeFi investment guide for someone with $10,000: where to stake, where to lend, where to provide liquidity. Use LIVE data from all protocols to back every recommendation.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","jito_stake_pool_stats","marinade_msol_apy","raydium_pools"),
          min_tools=5, has_n=True, min_w=200)),

    ("LF02", "I'm moving $100k from Ethereum to Solana DeFi. Analyze all major yield opportunities: lending (Solend/Kamino/MarginFi), LST staking (jitoSOL/mSOL), LP (Raydium/Orca). Show live APYs and recommend allocation.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","jito_stake_pool_stats","marinade_msol_apy","raydium_pools","orca_pools"),
          min_tools=5, has_n=True, min_w=200)),

    ("LF03", "Give me a Solana DeFi weekly digest: best performing protocols by TVL growth, top yield opportunities right now, notable token movements, MEV landscape, any protocol risks.",
     dict(want=("solend_stats","kamino_markets","raydium_info","marinade_stats","jito_mev_rewards","jup_trending"),
          min_tools=5, min_w=200)),

    ("LF04", "Build a DeFi risk scorecard for Solana: rate each major protocol (Solend, Kamino, MarginFi, Raydium, Orca, Marinade, Jito) on TVL, utilization, and health. Use live on-chain data.",
     dict(want=("solend_stats","kamino_markets","raydium_info","marinade_stats","orca_protocol_stats","jito_stake_pool_stats"),
          min_tools=5, has_n=True, min_w=200)),

    ("LF05", "Produce a yield farming alpha report: find the highest APY opportunities across lending, LP, and staking on Solana RIGHT NOW. Pull from every relevant protocol and build a ranked list.",
     dict(want=("solend_reserves","kamino_market_reserves","marginfi_banks","kamino_earn_vaults","raydium_pools","orca_pools","jito_stake_pool_stats","marinade_msol_apy"),
          min_tools=6, has_n=True, min_w=200)),
]

# ═══════════════════════════════════════════════════════════════════════════

async def main():
    print(f"\n{BOLD}{'═'*72}")
    print(f"  STRESS TEST ROUND 3 — {len(CASES)} cases — multi-protocol + limit stress")
    print(f"  Concurrency: {CONCURRENCY}  |  Timeout: {TIMEOUT}s per query")
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

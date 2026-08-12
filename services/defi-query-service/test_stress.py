"""
Parallel stress test — 200+ cases, all protocols, report failures only.
Concurrency: 5 simultaneous requests (OpenAI rate-limit safe).
"""
import asyncio, httpx, time, sys, json

BASE    = "http://localhost:3150"
TIMEOUT = 120
CONCURRENCY = 5   # simultaneous LLM calls

R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
W="\033[97m"; D="\033[90m"; E="\033[0m";  BOLD="\033[1m"

stats = {"pass": 0, "fail": 0, "error": 0}
fails = []
lock  = asyncio.Lock()

# ── helpers ──────────────────────────────────────────────────────────────────

async def ask(q: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                    r = await c.post(f"{BASE}/query", json={"question": q})
                    if r.status_code in (429, 500) and attempt < 2:
                        await asyncio.sleep(20 + attempt * 20)
                        continue
                    r.raise_for_status()
                    return r.json()
            except Exception as ex:
                if attempt < 2:
                    await asyncio.sleep(20)
                else:
                    return {"__err": str(ex), "plain": "", "tools_called": []}
        return {"__err": "max retries", "plain": "", "tools_called": []}

def tools_hit(called, *want):   return bool(set(called) & set(want))
def tools_none(called, *bad):   return not bool(set(called) & set(bad))
def has_words(plain, *words):   lo = plain.lower(); return [w for w in words if w.lower() not in lo]
def has_number(plain):          return any(c.isdigit() for c in plain)
def word_count(plain):          return len(plain.split())
def no_raw_error(plain):
    bad = ["internal server error","traceback","exception:","attributeerror","typeerror","temporarily unavailable"]
    lo = plain.lower(); return [b for b in bad if b in lo]

async def run(tc, question, sem,
              want=(),        # any of these tools must be called
              keywords=(),    # words in plain response
              min_w=0,
              no_tool=False,
              has_num=False):
    d = await ask(question, sem)
    plain   = d.get("plain", "")
    called  = d.get("tools_called", [])
    err_key = d.get("__err")

    passed_all = True
    reasons    = []

    # request must succeed
    if err_key:
        passed_all = False
        reasons.append(f"request_error: {err_key}")
    else:
        if no_tool:
            if called:
                passed_all = False
                reasons.append(f"expected_no_tool but called={called}")
        elif want:
            if not tools_hit(called, *want):
                passed_all = False
                reasons.append(f"tool_miss: called={called} want_any={list(want)}")

        if keywords:
            missing = has_words(plain, *keywords)
            if missing:
                passed_all = False
                reasons.append(f"keywords_missing={missing}")

        if min_w and word_count(plain) < min_w:
            passed_all = False
            reasons.append(f"too_short={word_count(plain)} (min {min_w})")

        if has_num and not has_number(plain):
            passed_all = False
            reasons.append("expected_number_in_response")

        raw = no_raw_error(plain)
        if raw:
            passed_all = False
            reasons.append(f"raw_error_in_response={raw}")

    async with lock:
        if passed_all:
            stats["pass"] += 1
            print(f"{G}✓{E} [{tc}]")
        else:
            stats["fail"] += 1
            detail = " | ".join(reasons)
            fails.append(f"[{tc}] {question[:70]!r}  →  {detail}")
            print(f"{R}✗{E} [{tc}] {detail[:100]}")

# ═══════════════════════════════════════════════════════════════════════════
# TEST CASES
# ═══════════════════════════════════════════════════════════════════════════

WALLET = "CakcnaRDHka2gXyfbEd2d3xsvkJkqsLw2akB3zsN1D2S"

CASES = [
    # ── JUPITER price & tokens ───────────────────────────────────────────────
    ("J01",  "What is the current price of SOL in USD?",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), keywords=("sol","usd"), has_num=True)),
    ("J02",  "Current price of BONK token",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), keywords=("bonk",), has_num=True)),
    ("J03",  "What is the price of WIF?",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), has_num=True)),
    ("J04",  "Get prices for SOL, USDC, and JUP simultaneously",
     dict(want=("jup_prices",), has_num=True)),
    ("J05",  "Show me trending tokens on Solana right now",
     dict(want=("jup_trending","birdeye_token_trending","dex_trending"), min_w=20)),
    ("J06",  "What are the top traded tokens by volume today?",
     dict(want=("jup_trending","birdeye_token_list","birdeye_token_trending"), min_w=15)),
    ("J07",  "List newly launched tokens on Solana",
     dict(want=("jup_recent","birdeye_new_listings","jup_new_tokens"), min_w=15)),
    ("J08",  "I want to swap 500 USDC for SOL — best route and price?",
     dict(want=("jup_quote",), keywords=("sol","usdc"), has_num=True, min_w=20)),
    ("J09",  "How much SOL will I get for 1000 USDC?",
     dict(want=("jup_quote",), has_num=True)),
    ("J10",  "Swap quote: 10 SOL to BONK",
     dict(want=("jup_quote",), has_num=True)),
    ("J11",  "Is the BONK token verified on Jupiter?",
     dict(want=("jup_search","jup_verify_eligibility"), keywords=("bonk",))),
    ("J12",  "Search for JUP token details — audit, holders, organic score",
     dict(want=("jup_search",), keywords=("jup",), min_w=20)),
    ("J13",  "What tokens have the highest organic score on Jupiter?",
     dict(want=("jup_trending","jup_search"), min_w=15)),
    ("J14",  "Find the mint address of WIF token",
     dict(want=("jup_search",), keywords=("wif",))),
    ("J15",  "What is the mint address of USDC on Solana?",
     dict(want=("jup_search",), has_num=False)),
    ("J16",  "Show me LST tokens available on Jupiter",
     dict(want=("jup_tokens_tag",), min_w=10)),
    ("J17",  "List verified tokens on Jupiter",
     dict(want=("jup_tokens_tag",), min_w=10)),

    # ── SOLEND / SAVE lending ────────────────────────────────────────────────
    ("S01",  "What is the SOL supply APY on Solend?",
     dict(want=("solend_reserves",), keywords=("sol","apy"), has_num=True, min_w=20)),
    ("S02",  "Show me all lending markets on Solend",
     dict(want=("solend_markets","solend_reserves"), min_w=20)),
    ("S03",  "What is the total TVL on Solend protocol?",
     dict(want=("solend_stats",), keywords=("tvl",), has_num=True)),
    ("S04",  "Solend protocol health — deposits, borrows, utilization",
     dict(want=("solend_stats",), has_num=True, min_w=30)),
    ("S05",  "What are the best interest rates on Solend right now?",
     dict(want=("solend_reserves",), keywords=("apy",), has_num=True, min_w=30)),
    ("S06",  "Show Solend daily stats for today",
     dict(want=("solend_daily_stats",), has_num=True)),
    ("S07",  "What fees did Solend generate this week?",
     dict(want=("solend_daily_fees","solend_stats"), min_w=15)),
    ("S08",  f"What are the Solend positions for wallet {WALLET}?",
     dict(want=("solend_user_overview",), min_w=15)),
    ("S09",  f"Is wallet {WALLET} at risk of liquidation on Solend?",
     dict(want=("solend_user_overview",), min_w=15)),
    ("S10",  f"How many claimable Solend rewards does {WALLET} have?",
     dict(want=("solend_confirmed_rewards",), min_w=10)),
    ("S11",  "Show jitoSOL, mSOL, bSOL LST rates on Solend",
     dict(want=("solend_lst_rates",), keywords=("sol",), has_num=True)),
    ("S12",  "What is the USDC borrow APY on Solend?",
     dict(want=("solend_reserves",), keywords=("usdc","apy"), has_num=True)),
    ("S13",  "Show Solend reward emission rates — liquidity mining APY",
     dict(want=("solend_reward_stats",), min_w=15)),
    ("S14",  "What isolated pools does Solend have?",
     dict(want=("solend_isolated_pool_stats","solend_markets"), min_w=15)),
    ("S15",  "Show me positions at risk of liquidation on Solend",
     dict(want=("solend_liquidation_attempts","solend_obligations_filtered"), min_w=15)),
    ("S16",  "What is the SLND / Save token circulating supply?",
     dict(want=("solend_circulating_supply","solend_total_supply"), has_num=True)),
    ("S17",  "Show Solend points leaderboard",
     dict(want=("solend_points_leaderboard",), min_w=10)),
    ("S18",  f"Check Solend VIP eligibility for wallet {WALLET}",
     dict(want=("solend_vip_eligibility",), min_w=10)),
    ("S19",  "What are the latest Solend announcements?",
     dict(want=("solend_announcements",), min_w=5)),
    ("S20",  "Show Solend changelog — what changed recently?",
     dict(want=("solend_changelogs",), min_w=5)),
    ("S21",  "Solend referral program stats and fees",
     dict(want=("solend_referral_stats","solend_referral_payments"), min_w=15)),
    ("S22",  "What is the SOL oracle price on Solend?",
     dict(want=("solend_prices",), has_num=True)),
    ("S23",  "Comprehensive Solend analysis: TVL, reserves, rewards, risks",
     dict(want=("solend_stats","solend_reserves"), keywords=("tvl","apy"), min_w=80)),

    # ── KAMINO ───────────────────────────────────────────────────────────────
    ("K01",  "What lending markets does Kamino have?",
     dict(want=("kamino_markets",), min_w=15)),
    ("K02",  "Show me Kamino supply and borrow APYs for SOL and USDC",
     dict(want=("kamino_market_reserves",), keywords=("sol","apy"), has_num=True)),
    ("K03",  "Best supply APY on Kamino right now",
     dict(want=("kamino_market_reserves",), has_num=True, min_w=20)),
    ("K04",  "List Kamino liquidity vaults and strategies",
     dict(want=("kamino_strategies",), min_w=20)),
    ("K05",  "Show Kamino Earn vaults — best yield vaults",
     dict(want=("kamino_earn_vaults",), min_w=20)),
    ("K06",  "Compare LST staking APYs on Kamino — jitoSOL vs mSOL vs bSOL",
     dict(want=("kamino_staking_yields",), has_num=True, min_w=20)),
    ("K07",  "What is the average LST staking yield on Kamino?",
     dict(want=("kamino_staking_yields","kamino_staking_yields_mean"), has_num=True)),
    ("K08",  f"Show my Kamino lending positions for wallet {WALLET}",
     dict(want=("kamino_user_obligations",), min_w=10)),
    ("K09",  f"What Kamino Earn positions does {WALLET} have?",
     dict(want=("kamino_user_positions",), min_w=10)),
    ("K10",  "What are Kamino leverage stats — average leverage?",
     dict(want=("kamino_leverage_stats",), min_w=10)),
    ("K11",  "What is the current Solana epoch number?",
     dict(want=("kamino_epoch_info",), has_num=True)),
    ("K12",  "Show Kamino oracle prices for SOL and USDC",
     dict(want=("kamino_oracle_prices",), has_num=True)),


    # ── MARINADE ─────────────────────────────────────────────────────────────
    ("MA01", "What is the Marinade mSOL APY over the last 30 days?",
     dict(want=("marinade_msol_apy",), keywords=("msol","apy"), has_num=True)),
    ("MA02", "Show Marinade protocol stats — TVL, mSOL price",
     dict(want=("marinade_stats",), has_num=True, min_w=15)),
    ("MA03", "List Marinade validators",
     dict(want=("marinade_validators",), min_w=20)),
    ("MA04", "What are Marinade validator scores?",
     dict(want=("marinade_validator_scores",), min_w=15)),
    ("MA05", "Show Marinade epoch staking rewards and MEV rewards",
     dict(want=("marinade_epoch_rewards",), min_w=15)),
    ("MA06", "What is Marinade native staking APY?",
     dict(want=("marinade_native_apy",), has_num=True)),
    ("MA07", "Show Marinade staking report — next epoch delegation plan",
     dict(want=("marinade_staking_report",), min_w=15)),
    ("MA08", "What is Solana cluster health and datacenter concentration?",
     dict(want=("marinade_cluster_stats",), min_w=20)),
    ("MA09", f"How much mSOL does wallet {WALLET} hold?",
     dict(want=("marinade_wallet_msol_balance",), min_w=10)),
    ("MA10", "What are Marinade commission changes recently?",
     dict(want=("marinade_commission_changes",), min_w=10)),

    # ── JITO ─────────────────────────────────────────────────────────────────
    ("JT01", "What is the current Jito tip floor?",
     dict(want=("jito_tip_floor",), has_num=True, keywords=("jito","tip"))),
    ("JT02", "Show Jito MEV rewards data",
     dict(want=("jito_mev_rewards","jito_daily_mev"), min_w=15)),
    ("JT03", "What is the jitoSOL APY and stake pool stats?",
     dict(want=("jito_stake_pool_stats",), has_num=True)),
    ("JT04", "Show jitoSOL to SOL ratio history",
     dict(want=("jito_jitosol_ratio",), has_num=True)),
    ("JT05", "How much SOL is staked with Jito validators?",
     dict(want=("jito_stake_pool_stats","jito_mev_rewards"), has_num=True)),

    # ── RAYDIUM ──────────────────────────────────────────────────────────────
    ("R01",  "Show top Raydium pools by TVL",
     dict(want=("raydium_pools",), has_num=True, min_w=20)),
    ("R02",  "Best Raydium pools by 24h volume",
     dict(want=("raydium_pools",), has_num=True, min_w=20)),
    ("R03",  "Show only Raydium CLMM concentrated liquidity pools",
     dict(want=("raydium_pools",), min_w=15)),
    ("R04",  "What are Raydium AMM standard pools?",
     dict(want=("raydium_pools",), min_w=15)),
    ("R05",  "Find Raydium pools containing SOL and USDC",
     dict(want=("raydium_pool_by_mint",), has_num=True, min_w=15)),
    ("R06",  "Show me the SOL-USDC pool TVL and APR on Raydium",
     dict(want=("raydium_pool_by_mint","raydium_pools"), has_num=True, min_w=20)),
    ("R07",  "How much SOL will I get for 100 USDC on Raydium?",
     dict(want=("raydium_swap_quote",), has_num=True)),
    ("R08",  "Raydium swap quote: 200 USDC → SOL, price impact and slippage",
     dict(want=("raydium_swap_quote",), has_num=True, min_w=20)),
    ("R09",  "What is Raydium total protocol TVL and volume?",
     dict(want=("raydium_info",), has_num=True)),
    ("R10",  "Show Raydium fee tiers for CLMM pools",
     dict(want=("raydium_clmm_config",), min_w=10)),
    ("R11",  "What are Raydium CPMM pool creation fees?",
     dict(want=("raydium_cpmm_config",), min_w=10)),
    ("R12",  "Show Raydium RAY staking pools",
     dict(want=("raydium_stake_pools",), min_w=10)),
    ("R13",  "Raydium recommended priority fee / auto fee",
     dict(want=("raydium_auto_fee",), has_num=True)),
    ("R14",  "Show Raydium token prices for SOL and BONK",
     dict(want=("raydium_prices",), has_num=True)),
    ("R15",  "Find BONK pools on Raydium",
     dict(want=("raydium_pool_by_mint",), keywords=("bonk",), min_w=15)),

    # ── BIRDEYE market data ───────────────────────────────────────────────────
    ("B01",  "Show me SOL token overview — price, market cap, volume",
     dict(want=("birdeye_token_overview","jup_search"), has_num=True, min_w=20)),
    ("B02",  "What is the BONK market cap?",
     dict(want=("birdeye_token_overview","birdeye_token_market_data","jup_prices"), has_num=True)),
    ("B03",  "OHLCV price chart data for SOL — last 24 hours",
     dict(want=("birdeye_ohlcv","birdeye_ohlcv_v1"), has_num=True)),
    ("B04",  "Show SOL/USDC trading pair overview on-chain",
     dict(want=("birdeye_pair_overview","dex_token","dex_search"), has_num=True, min_w=15)),
    ("B05",  "Recent transactions for SOL token",
     dict(want=("birdeye_token_txs","birdeye_txs_recent"), min_w=10)),
    ("B06",  "List top Solana tokens by market cap",
     dict(want=("birdeye_token_list","birdeye_token_market_data"), min_w=20)),
    ("B07",  "Show trending tokens on Birdeye",
     dict(want=("birdeye_token_trending","jup_trending","dex_trending"), min_w=15)),
    ("B08",  "What are the newest token listings on Solana?",
     dict(want=("birdeye_new_listings","jup_recent","jup_new_tokens"), min_w=15)),
    ("B09",  "Show BONK holder distribution and top holders",
     dict(want=("birdeye_token_holders","helius_token_holders","birdeye_holder_distribution"), keywords=("bonk",), min_w=15)),
    ("B10",  f"What is the net worth of wallet {WALLET}?",
     dict(want=("birdeye_wallet_current_net_worth","birdeye_wallet_net_worth_details"), has_num=True)),
    ("B11",  f"Show token balances for wallet {WALLET}",
     dict(want=("birdeye_wallet_token_list","helius_wallet_tokens","birdeye_wallet_current_net_worth"), min_w=15)),
    ("B12",  f"PnL summary for wallet {WALLET}",
     dict(want=("birdeye_wallet_pnl_summary","birdeye_wallet_pnl_details"), min_w=15)),
    ("B13",  "Show 24h price stats for SOL — high, low, volume",
     dict(want=("birdeye_price_stats","birdeye_price_volume"), has_num=True)),
    ("B14",  "SOL/USDC trading volume over last 24 hours",
     dict(want=("birdeye_price_volume","birdeye_pair_overview","dex_search"), has_num=True)),
    ("B15",  "Who are the top traders of BONK token?",
     dict(want=("birdeye_token_top_traders",), keywords=("bonk",), min_w=10)),
    ("B16",  "Is this token safe? Security check for BONK",
     dict(want=("birdeye_token_security","token_security","jup_search"), keywords=("bonk",), min_w=20)),
    ("B17",  "Show token metadata for SOL — name, symbol, description",
     dict(want=("birdeye_token_metadata","jup_search"), keywords=("sol",), min_w=10)),
    ("B18",  "Search for tokens related to 'dog' on Solana",
     dict(want=("birdeye_search","jup_search","dex_search"), min_w=10)),
    ("B19",  "Show recent mint and burn transactions for SOL",
     dict(want=("birdeye_mint_burn_txs",), min_w=10)),
    ("B20",  "What is the historical net worth trend for my wallet over 30 days?",
     dict(want=("birdeye_wallet_net_worth_history","birdeye_wallet_current_net_worth"), min_w=10)),

    # ── DEXSCREENER ──────────────────────────────────────────────────────────
    ("D01",  "Show trending pairs on DexScreener for Solana",
     dict(want=("dex_trending","jup_trending","birdeye_token_trending"), min_w=15)),
    ("D02",  "Search DexScreener for SOL/USDC trading pairs",
     dict(want=("dex_search","dex_token"), min_w=15)),
    ("D03",  "Show token analytics for BONK on DexScreener",
     dict(want=("dex_token","dex_search","birdeye_token_overview"), min_w=15)),
    ("D04",  "What DEX pairs exist for WIF token?",
     dict(want=("dex_token","dex_search","birdeye_token_overview"), keywords=("wif",), min_w=10)),

    # ── ORCA ─────────────────────────────────────────────────────────────────
    ("O01",  "Show top Orca pools by TVL",
     dict(want=("orca_pools",), has_num=True, min_w=20)),
    ("O02",  "What is Orca protocol total TVL and volume?",
     dict(want=("orca_protocol_stats",), has_num=True)),
    ("O03",  "Search for SOL-USDC pool on Orca",
     dict(want=("orca_pools_search","orca_pools"), min_w=10)),
    ("O04",  "What tokens are available on Orca?",
     dict(want=("orca_tokens","orca_tokens_search"), min_w=10)),
    ("O05",  "What is the ORCA token price and market cap?",
     dict(want=("orca_protocol_token","birdeye_token_overview","jup_prices"), has_num=True)),
    ("O06",  "What is ORCA circulating supply?",
     dict(want=("orca_circulating_supply","orca_total_supply"), has_num=True)),
    ("O07",  "Find Orca pools containing BONK",
     dict(want=("orca_pools_search","orca_tokens_search"), keywords=("bonk",), min_w=10)),

    # ── METEORA ──────────────────────────────────────────────────────────────
    ("ME01", "Show Meteora DLMM liquidity pools",
     dict(want=("meteora_pools",), min_w=15)),
    ("ME02", "Best yield pools on Meteora",
     dict(want=("meteora_pools",), min_w=15)),

    # ── HELIUS ───────────────────────────────────────────────────────────────
    ("H01",  "How many holders does BONK token have?",
     dict(want=("helius_token_holders","birdeye_token_holders","birdeye_holder_distribution"), has_num=True)),
    ("H02",  f"List all tokens in wallet {WALLET}",
     dict(want=("helius_wallet_tokens","birdeye_wallet_token_list","birdeye_wallet_current_net_worth"), min_w=15)),
    ("H03",  "Show token holder profile breakdown for JUP",
     dict(want=("birdeye_holder_profile","birdeye_holder_distribution","helius_token_holders"), min_w=10)),

    # ── MULTI-PROTOCOL COMPARISONS ───────────────────────────────────────────
    ("C03",  "Risk profile differences between Solend and Kamino",
     dict(want=("solend_reserves","kamino_market_reserves"), min_w=60)),
    ("C05",  "Compare jitoSOL APY on Solend vs Kamino vs Marinade",
     dict(want=("solend_lst_rates","kamino_staking_yields","marinade_msol_apy","jito_stake_pool_stats"), has_num=True, min_w=40)),
    ("C06",  "Which DEX has the best SOL-USDC liquidity — Raydium or Orca?",
     dict(want=("raydium_pool_by_mint","raydium_pools","orca_pools_search","orca_pools"), min_w=40)),
    ("C07",  "BONK vs WIF — price, volume, market cap comparison",
     dict(want=("jup_prices","birdeye_token_overview","birdeye_token_market_data"), min_w=40)),

    # ── WALLET ANALYSIS ──────────────────────────────────────────────────────
    ("W01",  f"Full DeFi overview for wallet {WALLET} — all positions",
     dict(want=("solend_user_overview","kamino_user_obligations","birdeye_wallet_current_net_worth"), min_w=20)),
    ("W02",  f"What is {WALLET} total portfolio value?",
     dict(want=("birdeye_wallet_current_net_worth","birdeye_wallet_token_list"), has_num=True)),
    ("W03",  f"Analyze trading performance (PnL) of wallet {WALLET}",
     dict(want=("birdeye_wallet_pnl_summary","birdeye_wallet_pnl_details"), min_w=20)),
    ("W04",  f"Has wallet {WALLET} made profit or loss trading?",
     dict(want=("birdeye_wallet_pnl_summary",), min_w=15)),

    # ── STAKING & LIQUID STAKING ─────────────────────────────────────────────
    ("ST01", "Best liquid staking APY on Solana — jitoSOL vs mSOL vs bSOL",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","marinade_stats","solend_lst_rates","kamino_staking_yields"), has_num=True, min_w=40)),
    ("ST02", "What is the mSOL to SOL exchange rate?",
     dict(want=("marinade_stats",), has_num=True)),
    ("ST03", "jitoSOL annual percentage yield",
     dict(want=("jito_stake_pool_stats","jito_jitosol_ratio"), has_num=True)),
    ("ST04", "Marinade vs Jito staking — which is better?",
     dict(want=("marinade_msol_apy","marinade_stats","jito_stake_pool_stats"), min_w=40)),

    # ── MARKET CONDITIONS ────────────────────────────────────────────────────
    ("MK01", "What is Solana ecosystem total DeFi TVL?",
     dict(want=("solend_stats","kamino_markets","marinade_stats","raydium_info"), has_num=True, min_w=30)),
    ("MK02", "Show me the most active Solana DeFi protocols today",
     dict(want=("solend_stats","kamino_markets","raydium_info","orca_protocol_stats"), min_w=30)),
    ("MK03", "What is current Solana transaction priority fee / tip?",
     dict(want=("jito_tip_floor","raydium_auto_fee"), has_num=True)),

    # ── TOKEN SECURITY & RISK ────────────────────────────────────────────────
    ("TS01", "Is BONK token safe to invest in? Audit and security check",
     dict(want=("birdeye_token_security","token_security","jup_search"), keywords=("bonk",), min_w=30)),
    ("TS02", "Check if this token has freeze authority or mint authority risks",
     dict(want=("jup_search","birdeye_token_security","token_security"), min_w=20)),
    ("TS03", "Is WIF a legitimate token? Check its audit",
     dict(want=("jup_search","birdeye_token_security","token_security"), keywords=("wif",), min_w=20)),

    # ── GENERAL DEFI KNOWLEDGE (no tool expected) ────────────────────────────
    ("GK01", "What is DeFi lending and how does it work?",
     dict(no_tool=True, min_w=50)),
    ("GK02", "Explain what impermanent loss is",
     dict(no_tool=True, min_w=40)),
    ("GK03", "What is a liquidity pool?",
     dict(no_tool=True, min_w=30)),
    ("GK04", "What does APY mean in DeFi?",
     dict(no_tool=True, min_w=20)),
    ("GK05", "What is a liquidation in DeFi lending?",
     dict(no_tool=True, min_w=30)),
    ("GK06", "What is slippage in a DEX swap?",
     dict(no_tool=True, min_w=20)),
    ("GK07", "Explain what an LST (liquid staking token) is",
     dict(no_tool=True, min_w=30)),

    # ── EDGE CASES ───────────────────────────────────────────────────────────
    ("E01",  "Price of an invalid token: mint address XXXINVALIDMINT123",
     dict(want=("jup_prices","birdeye_price","birdeye_token_overview"), min_w=10)),
    ("E02",  "Show Solend stats for fake wallet: NOTAVALIDWALLETADDRESS",
     dict(want=("solend_user_overview",), min_w=10)),
    ("E03",  "What is the price of a token that doesn't exist: ABC123FAKE",
     dict(min_w=5)),
    ("E04",  "Swap 0 USDC to SOL",
     dict(min_w=5)),
    ("E05",  "Show me swap routes for an absurd amount: 999999999 SOL to USDC",
     dict(want=("jup_quote","raydium_swap_quote"), min_w=10)),

    # ── SCENARIO: complex multi-step queries ─────────────────────────────────
    ("SC02", "Analyze the Solana DeFi ecosystem: top protocols by TVL, best yields, current market conditions",
     dict(want=("solend_stats","kamino_markets","raydium_info","marinade_stats"), min_w=100)),
    ("SC03", f"Give me a complete DeFi report for wallet {WALLET}: positions, PnL, net worth, risks",
     dict(want=("solend_user_overview","birdeye_wallet_current_net_worth","birdeye_wallet_pnl_summary"), min_w=40)),
    ("SC04", "Show me opportunities to earn yield with SOL: staking (jitoSOL/mSOL), lending (Solend/Kamino), LP (Raydium/Orca)",
     dict(want=("jito_stake_pool_stats","marinade_stats","solend_reserves","kamino_market_reserves","raydium_pools","orca_pools"), has_num=True, min_w=80)),
    ("SC05", "I have 10 SOL. Should I stake it, lend it, or provide liquidity? Compare APYs.",
     dict(want=("jito_stake_pool_stats","marinade_msol_apy","solend_reserves","kamino_market_reserves"), has_num=True, min_w=60)),
    ("SC06", "What happened on Solend today? Daily stats, fees, and any liquidations",
     dict(want=("solend_daily_stats","solend_daily_fees","solend_liquidation_attempts"), min_w=30)),
    ("SC07", "Compare BONK and WIF — which is more liquid, which has better fundamentals?",
     dict(want=("jup_prices","birdeye_token_overview","jup_search"), min_w=50)),

    # ── PUMPFUN / NEW TOKENS ─────────────────────────────────────────────────
    ("PF01", "Show me the newest pump.fun token launches",
     dict(want=("jup_recent","birdeye_new_listings","jup_new_tokens"), min_w=15)),
    ("PF02", "What are the latest token launches on Solana launchpads?",
     dict(want=("jup_recent","birdeye_new_listings"), min_w=15)),

    # ── PROTOCOL-SPECIFIC DEEP DIVES ─────────────────────────────────────────
    ("DP01", "Deep dive into Kamino protocol: markets, reserves, earn vaults, staking yields",
     dict(want=("kamino_markets","kamino_market_reserves","kamino_earn_vaults","kamino_staking_yields"), min_w=80)),
    ("DP02", "Full Raydium analysis: TVL, top pools by volume, AMM vs CLMM pools",
     dict(want=("raydium_info","raydium_pools"), has_num=True, min_w=60)),
    ("DP03", "Marinade staking full overview: APY, validators, TVL, epoch rewards",
     dict(want=("marinade_stats","marinade_msol_apy","marinade_validators"), has_num=True, min_w=60)),
    ("DP04", "Jito MEV ecosystem overview: tip floor, MEV rewards, jitoSOL APY",
     dict(want=("jito_tip_floor","jito_mev_rewards","jito_stake_pool_stats"), has_num=True, min_w=40)),
    ("DP05", "Orca protocol full analysis: TVL, top pools, ORCA token",
     dict(want=("orca_protocol_stats","orca_pools","orca_protocol_token"), has_num=True, min_w=60)),
]

# ═══════════════════════════════════════════════════════════════════════════
async def main():
    print(f"\n{BOLD}{'═'*70}")
    print(f"  PARALLEL STRESS TEST — {len(CASES)} cases — {CONCURRENCY} concurrent")
    print(f"{'═'*70}{E}\n")

    # health check
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            h = await c.get(f"{BASE}/health")
            print(f"  Service: {h.json()}\n")
    except Exception as e:
        print(f"{R}  Service unreachable: {e}{E}")
        sys.exit(1)

    sem = asyncio.Semaphore(CONCURRENCY)
    t0  = time.time()

    tasks = []
    for (tc, question, kwargs) in CASES:
        tasks.append(run(tc, question, sem, **kwargs))

    await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    total   = stats["pass"] + stats["fail"]
    pct     = int(stats["pass"] / total * 100) if total else 0

    print(f"\n{BOLD}{'═'*70}")
    print(f"  RESULT: {G}{stats['pass']} PASS{E}  {R}{stats['fail']} FAIL{E}  "
          f"— {BOLD}{pct}%{E}  ({elapsed/60:.1f} min)")
    print(f"{'═'*70}{E}")

    if fails:
        print(f"\n{R}{BOLD}  FAILURES ({len(fails)}):{E}")
        for f in sorted(fails):
            print(f"  {R}• {f}{E}")

    print()
    return 0 if stats["fail"] == 0 else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

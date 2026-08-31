"""Curated knowledge notes for protocols/topics that have no crawlable docs
(full SPAs, dead doc sites) or that the model should master directly.

Each note is written from OPRAI's own code + verified facts. Run this against the
prod Qdrant (via an SSH tunnel to 127.0.0.1:6333) with OPRAI_OPENAI_API_KEY set;
it deletes any existing chunks for each `source_id` first, so it is idempotent.
"""
import os, asyncio, hashlib
import crawl_all as C
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

NOTES = {
 # ── protocols with no crawlable docs ───────────────────────────────────────
 "poolstrade": ("pools.trade — Robinhood Chain token launchpad", """
# pools.trade

pools.trade is a token launchpad on the **Robinhood Chain** (an Arbitrum Nitro Orbit L2, chain id 4663). It lets anyone create and immediately trade a new token.

## How it works (the key difference from a bonding-curve launchpad)
Unlike Pons (which starts a token on a bonding curve), **pools.trade has NO bonding curve**. Every launch **creates a Uniswap V4 liquidity pool directly**, so the token trades on a standard automated market maker (AMM) from the very first block. Tokens are paired against **WETH** (the dominant quote asset on the Robinhood Chain). Because there is no bonding curve, there is **no graduation step** — the token is already a real DEX pool the moment it launches.

## The math
Trading follows **Uniswap V4 concentrated-liquidity AMM** math: price comes from the pool's `sqrtPriceX96` (price = (sqrtPriceX96 / 2^96)^2); liquidity is concentrated into tick ranges; within the active range the pool behaves like constant-product x · y = k, so each buy pushes price up and each sell pushes it down. Slippage/price impact depend on how much liquidity sits in the active tick range.

## In OPRAI
OPRAI discovers pools.trade launches on-chain (Blockscout launcher logs) and enriches them with DexScreener; trading executes through the token's Uniswap V4 pool.
"""),
 "pumpfun": ("pump.fun bonding curve — mechanics and math", """
# pump.fun bonding curve — mechanics and math

pump.fun is the dominant memecoin launchpad on **Solana**. Every token is a **fair launch**: no presale and no team allocation — everyone buys on the same bonding curve.

## The bonding curve (how price is set)
Each new token launches on a **constant-product bonding curve** with two virtual reserves held in an on-chain bonding-curve account: `virtual_token_reserves` and `virtual_sol_reserves`. The spot price in SOL is `virtual_sol_reserves / virtual_token_reserves`. Buying pushes tokens OUT and SOL IN, so token reserves fall and SOL reserves rise — price climbs along the curve. The product k = token_reserves × sol_reserves is held constant across a trade (constant-product, like a Uniswap x · y = k AMM). OPRAI reads these reserves **live from the chain** for the specific token (they are not fixed constants) to price every buy and sell.

## Graduation
When the bonding curve fills up (enough real SOL deposited), the token **graduates**: liquidity migrates out of the curve into a real DEX pool — historically a Raydium pool, now **PumpSwap** (pump.fun's own AMM). After graduation the token trades on the DEX like any other token, so OPRAI routes graduated tokens through the DEX (e.g. Jupiter / PumpSwap) instead of the curve.

## Fees & key point
A trade fee (~1%) is taken on buys/sells; some configs also have a creator fee. Before graduation the token is ONLY tradable on its pump.fun bonding curve; after graduation it is a normal DEX token.
"""),
 "solend": ("Solend — lending on Solana", """
# Solend — lending on Solana

Solend is a decentralized lending protocol on Solana. Users **supply** assets to earn interest and **borrow** other assets against that collateral.

## How it works (the math)
- Each asset lives in a **reserve** with its own supply and borrow APYs, set by a **utilization-based** interest-rate model: as more of the supplied liquidity is borrowed (higher utilization), borrow and supply rates rise; low utilization means low rates.
- Borrowing is **over-collateralized**. Each collateral asset has a **loan-to-value (LTV)** limit; your borrowing power is the sum of (collateral value × its LTV).
- **Health / liquidation**: if your borrowed value rises past your allowed limit — because collateral prices fell or debt interest accrued — your position becomes unhealthy and can be **liquidated**: a liquidator repays part of your debt and seizes collateral at a discount (the liquidation penalty).
- Interest accrues continuously; supplied balances grow and borrowed balances grow over time.
- Markets are isolated by **pool** (a main pool plus isolated pools), each with its own risk parameters and asset list.

## In OPRAI
OPRAI can supply, borrow, repay, and manage Solend positions, and read your health / borrow limit before you act.
"""),

 # ── OPRAI's own product + its home chain ───────────────────────────────────
 "oprai": ("OPRAI — the conversational AI layer for Web3", """
# OPRAI — the conversational AI layer for Web3

OPRAI turns plain language into on-chain action. You say what you want in a normal sentence and OPRAI plans it, fetches live on-chain data, prices and simulates it, builds the transaction, and hands it to **your own wallet** to sign. OPRAI is **non-custodial** — it never holds your funds; you sign every transaction.

## What you can do — one conversation, eight networks
Supported networks: **Solana, Ethereum, Base, BNB Chain, Polygon, Arbitrum, Optimism, and the Robinhood Chain.**
- **Swap** tokens (best route) and **bridge** across chains (via Relay on EVM, Jupiter/deBridge on Solana).
- **Trade perpetuals** (leveraged long/short) — Jupiter Perps on Solana, Lighter on the Robinhood Chain.
- **Launch a token** — pump.fun (Solana), Pons and pools.trade (Robinhood Chain).
- **Provide liquidity** into concentrated-liquidity pools — Orca, Raydium CLMM, Meteora DLMM, Uniswap V3/V4.
- **Lend / borrow** — Kamino, Morpho, Solend — and **liquid-stake** — Marinade, Jito, JupSOL, Sanctum.
- **Buy / sell NFTs** — Magic Eden, Tensor, OpenSea.
- **Read the chain** — a wallet's real P&L, smart-money flows, token risk / honeypot / rug checks, developer identity, holder distribution.

## How a transaction flows
1) You describe intent in words. 2) OPRAI parses it, picks the venue you named (or asks with 2–4 options if ambiguous), and fetches live state. 3) It builds quote → simulation → transaction. 4) You review and sign in your wallet. 5) OPRAI watches settlement and returns a receipt. A signature is never treated as success — it confirms only when the transaction actually settles.

## Good to know
- OPRAI answers questions about **live state** (balances, prices, pools, positions, holders) by querying the chain in real time — it does not invent numbers.
- On the Robinhood Chain it runs its **own always-current index** (blocks/txs/logs/transfers), which powers deep analytics — a "conversational Nansen".
- Reading and analysis are free; only the on-chain transactions you sign cost gas + any protocol/OPRAI fee.
- **$OPRAI** is OPRAI's own token, fair-launched on Pons on the Robinhood Chain — contract `0xd98e1e5a25702930b2fc92c15f3fef6d2987b5ac`.
"""),
 "robinhood_chain": ("The Robinhood Chain — ecosystem and assets", """
# The Robinhood Chain

The Robinhood Chain is an **Arbitrum Nitro Orbit L2** (an EVM chain, chain id **4663**). It is the chain OPRAI is most deeply integrated with, including OPRAI's own real-time on-chain analytics index.

## Assets and quote tokens
- **WETH** (`0x0bd7d308f8e1639fab988df18a8011f41eacad73`) is the dominant quote asset — most tokens trade against WETH.
- **USDG** (`0x5fc5360d0400a0fd4f2af552add042d716f1d168`) is the chain's USD stablecoin (≈ $1), used as quote/collateral on some venues (e.g. Lighter perps).
- Gas is paid in the chain's native ETH.

## Protocols live on the Robinhood Chain (all usable through OPRAI)
- **Pons** — a token launchpad with a bonding curve that graduates to a Uniswap V4 pool (~4.2 ETH).
- **pools.trade** — a launchpad with NO bonding curve; every launch is a Uniswap V4 pool directly.
- **Lighter** — non-custodial **order-book perpetuals** (crypto and tokenized stocks), collateralised in USDG.
- **Morpho** — lending markets and vaults (supply, borrow against collateral).
- **Uniswap** (V3/V4) and **SushiSwap** — AMM swaps and liquidity.
- **OpenSea** — NFT marketplace (Seaport).
- **Relay** — cross-chain and same-chain bridging/swaps.

## Why it matters
Because OPRAI indexes the whole chain in real time, it can answer deep questions from raw data — a wallet's true FIFO P&L, whether a token is a honeypot and its sell tax, who the developer is and whether they've rugged before, what smart money is accumulating — not guesses.
"""),

 # ── cross-cutting knowledge ────────────────────────────────────────────────
 "security": ("Token safety — honeypots, rugs, and how to check", """
# Token safety — honeypots, rugs, and how to check

Not every token is safe. OPRAI can analyze a token before you buy and warn you.

## Honeypot — can you actually sell?
A honeypot lets you BUY but blocks or heavily taxes SELLING. Check whether the token is sellable at all and what the **sell tax** is. On the Robinhood Chain, Pons tokens always allow selling on the curve, but the total fee = protocol fee + the creator's chosen **creator tax (0–10%)** — a very high sell tax is a red flag. A token can be toggled non-sellable AFTER launch, so the strongest proof it's currently exitable is "many different wallets sold it in the last 24 hours", not just "it was sellable once".

## Rug / dump risk signals
- **Holder concentration** — if the top 10 wallets hold most of the supply, a few sells can crash the price.
- **Developer holdings** — does the dev still hold a large share (can dump) or 0% (may have already exited)?
- **Developer history** — how many tokens has this dev launched and how many rugged? A serial rugger is a strong warning.
- **Launch bundle** — was a big % of supply bought in the launch block by insiders (a coordinated snipe)?
- **Authorities** — on Solana, can the mint authority mint more (dilute) or freeze accounts? On EVM, does an owner keep privileged functions (change tax, pause transfers)? Renounced authority/ownership is safer.
- **Liquidity** — is the LP locked, and is there enough depth to exit your size without huge slippage?

## How to verify
- Confirm the **contract is verified** on the block explorer.
- Verify the **contract address character-for-character** from an official source — look-alike addresses are a common scam.
- Ask OPRAI to run its token analysis (concentration, dev identity, rug/bundle detection, honeypot, risk score) before committing funds. OPRAI never confirms a token is safe just because a transaction succeeded.
"""),
 "defi_education": ("DeFi concepts — the essentials", """
# DeFi concepts — the essentials

## AMMs and price
An **automated market maker (AMM)** prices a token from a pool's reserves. The classic formula is constant product: x · y = k, so buying pushes the price up along a curve. **Concentrated liquidity** (Uniswap V3/V4, Orca whirlpools, Raydium CLMM, Meteora DLMM) lets liquidity providers focus capital in a price range for higher fee efficiency, but they earn fees only while the price is inside their range.

## Slippage and price impact
**Price impact** is how much your own trade moves the price (larger trade + thinner liquidity = more impact). **Slippage tolerance** is the max price change you'll accept between quote and execution — too low and the transaction fails, too high and you can be sandwiched by MEV bots.

## Impermanent loss (IL)
If you provide liquidity and the two assets' prices diverge, you end up with less value than if you'd simply held them — that gap is **impermanent loss**. It becomes permanent if you withdraw while diverged. Trading fees you earn can offset it.

## Lending, leverage, liquidation
**LTV (loan-to-value)** caps how much you can borrow against collateral. **Health factor** measures how close you are to liquidation; if collateral value falls or debt grows past the limit, you get **liquidated** — collateral sold at a penalty. **Leverage / multiply** loops borrow-and-redeposit to amplify exposure, which also amplifies liquidation risk.

## Perps and funding
**Perpetual futures** track a price with leverage and no expiry. A **funding rate** periodically transfers value between longs and shorts to keep the perp price near spot — positive funding means longs pay shorts.

## Rates and MEV
**APR** is the simple annual rate; **APY** compounds it. **MEV** (maximal extractable value) is value bots extract by reordering or inserting transactions (e.g. sandwich attacks) — a reason slippage settings and private routing matter.
"""),

 # ── trading / analysis education (neutral, educational — NOT financial advice) ──
 "technical_analysis": ("Technical analysis — reading price charts", """
# Technical analysis (TA) — reading price charts

Technical analysis studies past **price and volume** to estimate probabilities for future moves. It is a lens, not a crystal ball — this is education, **not financial advice**, and in crypto (thin, volatile, manipulable markets) indicators can fail.

## Candlesticks
Each candle shows four prices for a period: **open, high, low, close**. The **body** is open→close (green/up if close>open, red/down otherwise); the thin **wicks** are the high and low. Common single/multi-candle shapes people watch: **doji** (indecision), **hammer** (rejection of lower prices), **engulfing** (a large candle swallowing the previous one, a possible reversal).

## Trend, support and resistance
An **uptrend** makes higher highs and higher lows; a **downtrend** the reverse; sideways = range. **Support** is a price floor where buyers have stepped in before; **resistance** is a ceiling where sellers appeared. Broken support often flips to resistance and vice-versa.

## Indicators
- **Moving averages (MA)** — the average price over N periods; **SMA** is simple, **EMA** weights recent prices more. The 50 and 200 MAs are widely watched; a shorter MA crossing above a longer one is a "golden cross" (bullish by convention), below is a "death cross".
- **RSI** (Relative Strength Index, 0–100) — momentum; conventionally <30 is called "oversold" and >70 "overbought", but strong trends can stay extreme for a long time.
- **MACD** — the relationship between two EMAs; used to read momentum shifts and crossovers.
- **Volume** — confirms moves; a breakout on high volume is taken more seriously than one on low volume.

## Timeframes & caveats
Higher timeframes (4h, 1D) are generally more reliable than 1m. TA is **probabilistic**: indicators lag, patterns fail, and no signal guarantees an outcome. Combine it with fundamentals and risk management, never bet more than you can lose.
"""),
 "fundamental_analysis": ("Fundamental analysis — valuing a crypto token", """
# Fundamental analysis (FA) — is a token's value justified?

Fundamental analysis judges a token from its **supply, usage, and team** rather than its chart. Education, **not financial advice**.

## Valuation: market cap vs FDV
- **Market cap** = price × **circulating** supply — what the market currently values the float at.
- **FDV** (fully diluted valuation) = price × **total/max** supply — the value if every token were in circulation.
- A large gap (FDV ≫ market cap) means most tokens are **not yet circulating** — future unlocks/emissions can add sell pressure and dilute holders. A "low market cap" that hides a huge FDV is a common trap.

## Tokenomics
- **Supply**: circulating / total / max; is it fixed or inflationary?
- **Emissions / inflation**: new tokens minted over time (e.g. LP or staking rewards) dilute holders unless demand grows faster.
- **Vesting & unlocks**: team/investor allocations that unlock on a schedule — a **cliff** unlock can dump large supply at once; check the unlock calendar.
- **Allocation**: how much went to team, investors, treasury, community — heavy insider allocation is a risk.

## Usage & value capture
- **TVL** (total value locked), active users, and **volume** show real usage.
- **Protocol revenue / fees** — does the protocol earn real money, and does the token capture any of it ("real yield" vs pure emissions)? A mcap-to-fees ratio is a rough "P/S".
- **Holder distribution** — concentration among a few whales is fragile.
- **Utility & moat** — what does the token actually do (governance, fees, staking, collateral), and can competitors copy it?

## Caveat
Narratives and hype move crypto short-term; fundamentals matter more over time. FA informs, it does not decide — do your own research.
"""),
 "risk_management": ("Risk management and trading discipline", """
# Risk management and trading discipline

The single biggest difference between surviving and blowing up is **risk management**, not picking winners. This is education, **not financial advice**.

## Core rules
- **Only invest what you can afford to lose.** Crypto is high-risk and can go to zero.
- **Position sizing** — risk only a small fraction of your capital on any single trade/token, so no one loss is fatal.
- **Stop-loss** — decide your exit *before* you enter; a plan removes emotion. Weigh **risk/reward**: risking $1 to make $3 (1:3) needs to be right far less often than 1:1.
- **Don't over-leverage** — leverage multiplies gains AND losses, and a small adverse move can **liquidate** the whole position.
- **Diversify** — don't put everything in one token or one chain.
- **Take profits** — unrealized gains are not real until you exit.

## Psychology (the quiet account-killers)
- **FOMO** (chasing a pump), **revenge trading** (over-sizing to win back a loss), and **confirmation bias** (only reading bullish takes) destroy more accounts than bad analysis.

## Security is risk management too
Self-custody your keys, verify contract addresses from official sources, check a token for honeypot/rug signals before buying, and never sign a transaction you don't understand.
"""),
 "defi_advanced": ("DeFi in depth — yield, staking, stablecoins, bridges, oracles", """
# DeFi in depth

## Where yield comes from
Real DeFi yield has sources: **trading fees** (to liquidity providers), **lending interest** (from borrowers), **staking rewards** (for securing a chain), and **incentive emissions** (a protocol printing its own token to attract liquidity). "**Real yield**" is paid from actual fees/revenue; emission yield can evaporate and dilute you — always ask *where the yield comes from*.

## Staking and liquid staking
- **Native staking** locks a token to secure the network for rewards (illiquid while staked).
- **Liquid staking** gives you a tradeable receipt token — **LSTs** like mSOL (Marinade), JitoSOL (Jito), JupSOL, or Sanctum's — so you earn staking yield and can still use the token in DeFi.
- **Restaking / LRTs** reuse staked assets to secure additional services for extra yield and extra risk.

## Stablecoins (not all equal)
- **Fiat-backed** (USDC, USDT, USDG) — a company holds reserves; risk is trust/regulation/de-peg.
- **Crypto-collateralized** (e.g. DAI) — over-collateralized by crypto; risk is collateral crashes.
- **Algorithmic** — maintain the peg with mechanisms/other tokens; historically the riskiest (some have collapsed to zero).

## Infrastructure & risks
- **Bridges** move assets between chains; they are a top hack target — prefer well-audited routes (OPRAI uses Relay on EVM).
- **Oracles** feed off-chain prices on-chain; a manipulated oracle can drain a lending market.
- **Smart-contract risk** — bugs/exploits; prefer audited, battle-tested protocols.
- **Delta-neutral / looping** — hedged or leveraged-loop strategies that chase yield while trying to limit price exposure; they add liquidation and complexity risk.
- **Governance** — token-holders vote on parameters; concentrated voting power is a centralization risk.
"""),
}

TAGS = {
 "poolstrade": ["poolstrade","pools.trade","launchpad","robinhood-chain","uniswap-v4"],
 "pumpfun": ["pumpfun","bonding-curve","launchpad","solana","graduation"],
 "solend": ["solend","lending","borrow","solana","liquidation","ltv"],
 "oprai": ["oprai","product","how-to","multichain","conversational","non-custodial"],
 "robinhood_chain": ["robinhood-chain","chain-4663","usdg","weth","ecosystem"],
 "security": ["security","honeypot","rug","scam","risk","safety"],
 "defi_education": ["defi","education","amm","impermanent-loss","slippage","liquidation","perps","apy"],
 "technical_analysis": ["technical-analysis","ta","chart","candlestick","rsi","macd","moving-average","trading"],
 "fundamental_analysis": ["fundamental-analysis","fa","tokenomics","market-cap","fdv","unlocks","tvl","valuation"],
 "risk_management": ["risk-management","position-sizing","stop-loss","leverage","psychology","trading"],
 "defi_advanced": ["defi","yield","staking","lst","restaking","stablecoin","bridge","oracle","governance"],
}


async def main():
    oai = AsyncOpenAI(api_key=os.environ["OPRAI_OPENAI_API_KEY"])
    qd = AsyncQdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    for proto, (title, body) in NOTES.items():
        await qd.delete("oprai_blockchain_knowledge", points_selector=models.Filter(
            must=[models.FieldCondition(key="source_id", match=models.MatchValue(value=f"{proto}_curated"))]))
        src = C.Source(f"{proto}_curated", "protocols", "direct_urls",
                       f"curated://{proto}", proto, "curated_knowledge", "curated",
                       tags=TAGS.get(proto, [proto, "curated"]))
        chunks = C.chunk_text(body.strip(), f"{proto}_curated", [{"label": "Overview", "anchor": ""}])
        for c in chunks:
            c["url"] = f"curated://{proto}"
        ph = hashlib.md5(body.encode()).hexdigest()
        await C.upsert_chunks(qd, oai, chunks, src, title=title, page_hash=ph)
        print(f"  {proto}: {len(chunks)} curated chunks upserted")


if __name__ == "__main__":
    asyncio.run(main())

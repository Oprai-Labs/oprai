import os, asyncio, hashlib
import crawl_all as C
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

NOTES = {
 "poolstrade": ("pools.trade — Robinhood Chain token launchpad", """
# pools.trade

pools.trade is a token launchpad on the **Robinhood Chain** (an Arbitrum Nitro Orbit L2, chain id 4663). It lets anyone create and immediately trade a new token.

## How it works (the key difference from a bonding-curve launchpad)
Unlike Pons (which starts a token on a bonding curve), **pools.trade has NO bonding curve**. Every launch **creates a Uniswap V4 liquidity pool directly**, so the token trades on a standard automated market maker (AMM) from the very first block. Tokens are paired against **WETH** (the dominant quote asset on the Robinhood Chain).

Because there is no bonding curve, there is **no graduation step** — the token is already a real DEX pool the moment it launches.

## The math
Trading follows **Uniswap V4 concentrated-liquidity AMM** math:
- Price is derived from the pool's `sqrtPriceX96` (price = (sqrtPriceX96 / 2^96)^2).
- Liquidity is concentrated into tick ranges; within the active range the pool behaves like a constant-product market maker, x · y = k, so each buy pushes the price up along the curve and each sell pushes it down.
- Slippage and price impact depend on how much liquidity sits in the active tick range — thin liquidity means large price impact.

## In OPRAI
OPRAI discovers pools.trade launches on-chain (Blockscout launcher logs) and enriches them with DexScreener; trading is executed through the token's Uniswap V4 pool. Since it is a V4 pool from launch, everything that applies to a Uniswap V4 swap (routing, slippage, fee tier) applies to a pools.trade token.
"""),
 "pumpfun": ("pump.fun bonding curve — mechanics and math", """
# pump.fun bonding curve — mechanics and math

pump.fun is the dominant memecoin launchpad on **Solana**. Every token is a **fair launch**: no presale and no team allocation — everyone buys on the same bonding curve.

## The bonding curve (how price is set)
Each new token launches on a **constant-product bonding curve** with two virtual reserves held in an on-chain bonding-curve account: `virtual_token_reserves` and `virtual_sol_reserves`. The spot price of the token in SOL is:

    price = virtual_sol_reserves / virtual_token_reserves

Buying pushes tokens OUT of the curve and SOL IN, so `virtual_token_reserves` falls and `virtual_sol_reserves` rises — the price climbs along the curve. Selling does the reverse. The product k = virtual_token_reserves × virtual_sol_reserves is held constant across a trade (constant-product), exactly like a Uniswap-style x · y = k AMM, which is what makes the price rise smoothly as more is bought. OPRAI reads these reserves **live from the chain** for the specific token (they are not fixed constants) to price every buy and sell.

## Graduation
When the bonding curve fills up — i.e. enough real SOL has been deposited into it — the token **graduates**: its liquidity is migrated out of the bonding curve into a real DEX pool. Historically this was a Raydium pool; pump.fun now migrates to **PumpSwap** (pump.fun's own AMM). After graduation the token no longer trades on the bonding curve — it trades on the DEX pool like any other token, so OPRAI routes graduated tokens through the DEX (e.g. Jupiter / PumpSwap) instead of the curve.

## Fees
A trade fee (on the order of ~1%) is taken on bonding-curve buys and sells. Creators can also earn a creator fee on some configurations.

## Key point for users
Before graduation the token is ONLY tradable on its pump.fun bonding curve (price rises with each buy along the constant-product curve); after graduation it is a normal DEX token. "Can I sell / what's the price" is answered from the live curve reserves pre-graduation, and from the DEX pool post-graduation.
"""),
}

async def main():
    oai = AsyncOpenAI(api_key=os.environ["OPRAI_OPENAI_API_KEY"])
    qd = AsyncQdrantClient(url="http://localhost:6333")
    for proto,(title,body) in NOTES.items():
        # remove existing curated chunks for idempotency
        from qdrant_client.http import models
        await qd.delete("oprai_blockchain_knowledge", points_selector=models.Filter(
            must=[models.FieldCondition(key="source_id", match=models.MatchValue(value=f"{proto}_curated"))]))
        src = C.Source(f"{proto}_curated", "protocols", "direct_urls",
                       f"curated://{proto}", proto, "curated_knowledge", "curated",
                       tags=[proto, "curated"])
        chunks = C.chunk_text(body.strip(), f"{proto}_curated", [{"label":"Overview","anchor":""}])
        for c in chunks: c["url"] = f"curated://{proto}"
        ph = hashlib.md5(body.encode()).hexdigest()
        await C.upsert_chunks(qd, oai, chunks, src, title=title, page_hash=ph)
        print(f"  {proto}: {len(chunks)} curated chunks upserted")

asyncio.run(main())

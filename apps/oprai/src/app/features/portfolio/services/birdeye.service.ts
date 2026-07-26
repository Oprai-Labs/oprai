import { Injectable } from '@angular/core';

export interface BirdeyeTokenPrice {
  price: number;
  change24h: number | null;
}

export interface DexScreenerTokenMeta {
  name: string;
  symbol: string;
  imageUrl: string | null;
}

/**
 * Token price service using DexScreener API (free, no auth, CORS-friendly).
 * Named BirdeyeService for backward compatibility with existing imports.
 */
const DEXSCREENER_URL = 'https://api.dexscreener.com/latest/dex/tokens';
const CACHE_TTL = 60_000;
const BATCH_SIZE = 30;

@Injectable({ providedIn: 'root' })
export class BirdeyeService {
  private cache = new Map<string, { data: BirdeyeTokenPrice; ts: number }>();
  private metaCache = new Map<string, DexScreenerTokenMeta>();

  async getTokenPrices(mints: string[]): Promise<Map<string, BirdeyeTokenPrice>> {
    const result = new Map<string, BirdeyeTokenPrice>();
    if (mints.length === 0) return result;

    const now = Date.now();
    const toFetch: string[] = [];

    for (const mint of mints) {
      const cached = this.cache.get(mint);
      if (cached && now - cached.ts < CACHE_TTL) {
        result.set(mint, cached.data);
      } else {
        toFetch.push(mint);
      }
    }

    if (toFetch.length === 0) return result;

    for (let i = 0; i < toFetch.length; i += BATCH_SIZE) {
      const batch = toFetch.slice(i, i + BATCH_SIZE);
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        const res = await fetch(`${DEXSCREENER_URL}/${batch.join(',')}`, {
          signal: controller.signal,
        });
        clearTimeout(timeout);
        if (!res.ok) continue;

        interface DexPair {
          chainId: string;
          baseToken?: { address?: string; name?: string; symbol?: string };
          priceUsd?: string;
          priceChange?: { h24?: number };
          liquidity?: { usd?: number };
          info?: { imageUrl?: string };
        }
        const json = (await res.json()) as { pairs?: DexPair[] };
        const pairs: DexPair[] = json.pairs ?? [];

        // For each queried mint, find the highest-liquidity Solana pair
        const bestPairByMint = new Map<string, DexPair>();
        for (const pair of pairs) {
          if (pair.chainId !== 'solana') continue;

          const baseAddr = pair.baseToken?.address;
          if (!baseAddr || !batch.includes(baseAddr)) continue;

          const existing = bestPairByMint.get(baseAddr);
          const liq = pair.liquidity?.usd ?? 0;
          if (!existing || liq > (existing.liquidity?.usd ?? 0)) {
            bestPairByMint.set(baseAddr, pair);
          }
        }

        for (const [mint, pair] of bestPairByMint) {
          if (pair.priceUsd) {
            const tp: BirdeyeTokenPrice = {
              price: parseFloat(pair.priceUsd),
              change24h:
                typeof pair.priceChange?.h24 === 'number'
                  ? pair.priceChange.h24
                  : null,
            };
            this.cache.set(mint, { data: tp, ts: now });
            result.set(mint, tp);
          }
          // Extract token metadata from DexScreener response
          const baseToken = pair.baseToken;
          if (baseToken?.name && baseToken?.symbol) {
            this.metaCache.set(mint, {
              name: baseToken.name,
              symbol: baseToken.symbol,
              imageUrl: pair.info?.imageUrl ?? null,
            });
          }
        }
      } catch {
        // Skip failed batch
      }
    }

    // Second pass: DexScreener caps per-request pairs at ~30 ranked by
    // global liquidity, so SOL+USDC will always crowd out smaller mints in
    // a batched response — even when the pair exists. Re-query each
    // missing mint solo so memecoins / Pump.fun tokens with low-but-real
    // liquidity get their price.
    const stillMissingAfterBatch = mints.filter(m => !result.has(m));
    if (stillMissingAfterBatch.length > 0) {
      // Cap parallelism to avoid hammering DexScreener; their public API
      // throttles per-IP at ~300 req/min.
      const PARALLEL = 6;
      for (let i = 0; i < stillMissingAfterBatch.length; i += PARALLEL) {
        const slice = stillMissingAfterBatch.slice(i, i + PARALLEL);
        await Promise.all(slice.map(async mint => {
          try {
            const r = await fetch(`${DEXSCREENER_URL}/${mint}`);
            if (!r.ok) return;
            const j = await r.json() as { pairs?: Array<{
              chainId: string;
              baseToken?: { address?: string; name?: string; symbol?: string };
              priceUsd?: string;
              priceChange?: { h24?: number };
              liquidity?: { usd?: number };
              info?: { imageUrl?: string };
            }> };
            // CRITICAL: only consider pairs where the QUERIED mint is the BASE
            // token. DexScreener returns every pair the token appears in —
            // including ones where it's the QUOTE (e.g. BTC/USDT for the USDT
            // mint). Without this filter the highest-liquidity pair for a
            // quote-heavy token like USDT is a BTC/USDT pool, and we'd wrongly
            // read the BASE token's price + name — that's how 0.1 USDT rendered
            // as "Bitcoin / BTC" worth $70k. The batch pass already filters this
            // way; the solo pass must match. If nothing base-matches, the mint
            // stays missing and the Jupiter Lite Price third pass prices it.
            const solPairs = (j.pairs ?? []).filter(
              p => p.chainId === 'solana' && p.baseToken?.address === mint,
            );
            // Pick highest-liquidity pair for this mint.
            const best = solPairs.reduce<typeof solPairs[number] | null>(
              (acc, p) => (!acc || (p.liquidity?.usd ?? 0) > (acc.liquidity?.usd ?? 0)) ? p : acc,
              null,
            );
            if (best?.priceUsd) {
              const tp: BirdeyeTokenPrice = {
                price: parseFloat(best.priceUsd),
                change24h: typeof best.priceChange?.h24 === 'number' ? best.priceChange.h24 : null,
              };
              this.cache.set(mint, { data: tp, ts: now });
              result.set(mint, tp);
            }
            const baseToken = best?.baseToken;
            if (baseToken?.name && baseToken?.symbol && !this.metaCache.has(mint)) {
              this.metaCache.set(mint, {
                name: baseToken.name,
                symbol: baseToken.symbol,
                imageUrl: best?.info?.imageUrl ?? null,
              });
            }
          } catch {
            // Single-mint hiccup — Jupiter fallback below catches it.
          }
        }));
      }
    }

    // Third pass: fall back to Jupiter Lite Price for any mint *both*
    // DexScreener passes missed. Pump.fun launches that haven't graduated
    // to a Solana DEX pair yet still route through Jupiter's bonding-curve
    // quoter, so this is the last reliable price source.
    const stillMissing = mints.filter(m => !result.has(m));
    if (stillMissing.length > 0) {
      try {
        const jupRes = await this.fetchJupiterLitePrices(stillMissing);
        for (const [mint, tp] of jupRes) {
          this.cache.set(mint, { data: tp, ts: now });
          result.set(mint, tp);
        }
      } catch {
        // Network blip — caller already has whatever DexScreener returned.
      }
    }

    return result;
  }

  /**
   * Jupiter Lite Price v3. Free, no auth, batch up to 100 mint ids per
   * request. Returns USD price + 24h change derived from Jupiter's routes
   * (bonding-curve math for pump.fun, AMM TWAP elsewhere). The previous v2
   * URL was retired in early 2026 — v3 returns a flat object keyed by
   * mint with `usdPrice` + `priceChange24h` (already a percentage, e.g.
   * -0.07 means -0.07%, not -7%).
   * https://lite-api.jup.ag/price/v3?ids=mint1,mint2
   */
  private async fetchJupiterLitePrices(mints: string[]): Promise<Map<string, BirdeyeTokenPrice>> {
    const out = new Map<string, BirdeyeTokenPrice>();
    if (!mints.length) return out;

    const BATCH = 100;
    for (let i = 0; i < mints.length; i += BATCH) {
      const batch = mints.slice(i, i + BATCH);
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const res = await fetch(
          `https://lite-api.jup.ag/price/v3?ids=${batch.join(',')}`,
          { signal: controller.signal },
        );
        clearTimeout(timeout);
        if (!res.ok) continue;

        interface JupV3Price {
          usdPrice?: number;
          priceChange24h?: number;
          liquidity?: number;
          decimals?: number;
        }
        // v3 returns a flat map (no `data` wrapper). Tokens with no
        // liquidity are present in the response but lack `usdPrice`.
        const json = (await res.json()) as Record<string, JupV3Price | null>;
        for (const mint of batch) {
          const entry = json[mint];
          if (!entry || typeof entry.usdPrice !== 'number') continue;
          const price = entry.usdPrice;
          if (!Number.isFinite(price) || price <= 0) continue;
          const changePct = entry.priceChange24h;
          const change24h = typeof changePct === 'number' && Number.isFinite(changePct)
            ? changePct
            : null;
          out.set(mint, { price, change24h });
        }
      } catch {
        clearTimeout(timeout);
      }
    }
    return out;
  }

  async getPriceChanges(mints: string[]): Promise<Map<string, number>> {
    const tokenPrices = await this.getTokenPrices(mints);
    const out = new Map<string, number>();
    for (const [mint, tp] of tokenPrices) {
      if (tp.change24h !== null) out.set(mint, tp.change24h);
    }
    return out;
  }

  async getPrices(mints: string[]): Promise<Map<string, number>> {
    const tokenPrices = await this.getTokenPrices(mints);
    const out = new Map<string, number>();
    for (const [mint, tp] of tokenPrices) out.set(mint, tp.price);
    return out;
  }

  /**
   * Get token metadata extracted from DexScreener responses.
   * Available after getTokenPrices() has been called.
   */
  getTokenMeta(mint: string): DexScreenerTokenMeta | null {
    return this.metaCache.get(mint) ?? null;
  }

  getAllTokenMeta(): Map<string, DexScreenerTokenMeta> {
    return this.metaCache;
  }

  /**
   * Wipe the in-memory price cache. Called on a manual portfolio refresh so
   * a previously-partial fetch (DexScreener dropped pump mints; Jupiter v3
   * timed out) doesn't get re-served from cache for the next 60s. The
   * metadata cache is preserved — symbols + names rarely change and are
   * useful even when prices missed.
   */
  clearCache(): void {
    this.cache.clear();
  }
}

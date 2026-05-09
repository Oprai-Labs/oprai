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

    return result;
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
}

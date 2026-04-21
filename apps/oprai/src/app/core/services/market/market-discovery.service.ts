import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { CacheStore } from '../../utils/cache-store';

export interface TrendingToken {
  url: string;
  chainId: string;
  tokenAddress: string;
  icon?: string;
  header?: string;
  description?: string;
  totalAmount?: number;
  amount?: number;
}

export interface DexPair {
  chainId: string;
  dexId: string;
  url: string;
  pairAddress: string;
  baseToken: {
    address: string;
    name: string;
    symbol: string;
  };
  quoteToken: {
    address: string;
    name: string;
    symbol: string;
  };
  priceNative: string;
  priceUsd: string;
  txns: {
    m5: { buys: number; sells: number };
    h1: { buys: number; sells: number };
    h6: { buys: number; sells: number };
    h24: { buys: number; sells: number };
  };
  volume: { h24: number; h6: number; h1: number; m5: number };
  priceChange: { m5: number; h1: number; h6: number; h24: number };
  liquidity?: { usd: number; base: number; quote: number };
  pairCreatedAt?: number;
  fdv?: number;
  marketCap?: number;
  info?: {
    imageUrl?: string;
    websites?: { label: string; url: string }[];
    socials?: { type: string; url: string }[];
  };
}

export interface TrendingData {
  boosted: TrendingToken[];
  trending: TrendingToken[];
}

@Injectable({ providedIn: 'root' })
export class MarketDiscoveryService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBase;

  private readonly cache = new CacheStore<any>(60_000); // 60s default

  /** Fetch trending tokens from chat-service (DexScreener). */
  async getTrending(): Promise<TrendingData> {
    const cached = this.cache.get('trending');
    if (cached) return cached;

    try {
      // Use chat-service trending API
      const data: TrendingData = {
        boosted: [],
        trending: [],
      };

      // Try to fetch from tokens/trending if available
      try {
        const tokensResp = await firstValueFrom(
          this.http.get<any>(`${this.apiBase}/tokens/trending?category=all`)
        );
        if (tokensResp?.hot) {
          data.trending = tokensResp.hot.map((t: any) => ({
            tokenAddress: t.address,
            header: t.name,
            symbol: t.symbol,
            price: t.price,
            priceChange: t.price_change_h24,
            volume: t.volume_24h,
            liquidity: t.liquidity_usd,
          }));
        }
      } catch {
        // Fall back to empty if endpoint not available
      }

      this.cache.set('trending', data);
      return data;
    } catch (err) {
      console.error('Trending fetch error:', err);
      return { boosted: [], trending: [] };
    }
  }

  /** Fetch DexScreener pair data for a token mint. Returns best pairs sorted by liquidity. */
  async getTokenPairs(mint: string): Promise<DexPair[]> {
    const cacheKey = `pairs:${mint}`;
    const cached = this.cache.get(cacheKey);
    if (cached) return cached;

    try {
      const pairs = await firstValueFrom(
        this.http.get<DexPair[]>(`${this.apiBase}/market/pairs/${mint}`)
      );
      const solanaPairs = (pairs ?? [])
        .filter((p: DexPair) => p.chainId === 'solana')
        .sort((a: DexPair, b: DexPair) => (b.liquidity?.usd ?? 0) - (a.liquidity?.usd ?? 0));
      this.cache.set(cacheKey, solanaPairs);
      return solanaPairs;
    } catch (err) {
      console.error('Pairs fetch error:', err);
      return [];
    }
  }

  /** Extract top gainers from trending pairs. */
  async getTopGainers(pairs: DexPair[]): Promise<DexPair[]> {
    return [...pairs]
      .filter(p => p.priceChange?.h24 > 0)
      .sort((a, b) => (b.priceChange?.h24 ?? 0) - (a.priceChange?.h24 ?? 0))
      .slice(0, 20);
  }

  /** Extract top losers from trending pairs. */
  async getTopLosers(pairs: DexPair[]): Promise<DexPair[]> {
    return [...pairs]
      .filter(p => p.priceChange?.h24 < 0)
      .sort((a, b) => (a.priceChange?.h24 ?? 0) - (b.priceChange?.h24 ?? 0))
      .slice(0, 20);
  }

  /** Get volume leaders from pairs. */
  async getVolumeLeaders(pairs: DexPair[]): Promise<DexPair[]> {
    return [...pairs]
      .sort((a, b) => (b.volume?.h24 ?? 0) - (a.volume?.h24 ?? 0))
      .slice(0, 20);
  }

}

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { CacheStore } from '../../utils/cache-store';

export interface DexScreenerPair {
  chainId: string;
  pairAddress: string;
  dexId: string;
  priceUsd?: string;
  priceChange?: { h24?: number; h6?: number; h1?: number };
  volume?: { h24?: number };
  liquidity?: { usd?: number };
  marketCap?: number;
  fdv?: number;
  txns?: { h24?: { buys: number; sells: number } };
  info?: { imageUrl?: string };
}

interface DexScreenerResponse {
  pairs?: DexScreenerPair[];
}

interface BirdeyeOverview {
  address?: string;
  symbol?: string;
  name?: string;
  decimals?: number;
  price?: number;
  priceChange24hPercent?: number;
  v24hUSD?: number;
  mc?: number;
  fdv?: number;
  liquidity?: number;
  holder?: number;
  supply?: number;
  circulatingSupply?: number;
  trade24h?: number;
  trade24hChangePercent?: number;
  buy24h?: number;
  sell24h?: number;
  uniqueWallet24h?: number;
  logoURI?: string;
  description?: string | null;
  extensions?: { website?: string };
}

interface AnalyticsApiResponse {
  dexscreener?: DexScreenerResponse | DexScreenerPair[];
  birdeye?: { data?: BirdeyeOverview } & BirdeyeOverview;
}

export interface TokenAnalytics {
  // DexScreener data
  pairs?: DexScreenerPair[];
  // Birdeye overview
  overview?: {
    address?: string;
    symbol?: string;
    name?: string;
    decimals?: number;
    price?: number;
    priceChange24h?: number;
    volume24h?: number;
    marketCap?: number;
    fdv?: number;
    liquidity?: number;
    holder?: number;
    supply?: number;
    circulatingSupply?: number;
    trade24h?: number;
    trade24hChangePercent?: number;
    buy24h?: number;
    sell24h?: number;
    uniqueWallet24h?: number;
    logoURI?: string;
    description?: string | null;
    websites?: string[];
    socials?: { type: string; url: string }[];
  } | undefined;
  // Best pair from DexScreener
  bestPair?: {
    priceUsd: number;
    priceChange24h: number;
    volume24h: number;
    liquidity: number;
    marketCap: number;
    fdv: number;
    pairAddress: string;
    dexId: string;
    txns24h: { buys: number; sells: number };
    imageUrl?: string | null;
  } | undefined;
}

@Injectable({ providedIn: 'root' })
export class TokenAnalyticsService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBase;

  private readonly cache = new CacheStore<TokenAnalytics>(120_000); // 2 min

  /** Get comprehensive analytics for a token. */
  async getAnalytics(mint: string): Promise<TokenAnalytics> {
    const cached = this.cache.get(mint);
    if (cached) return cached;

    try {
      const resp = await firstValueFrom(
        this.http.get<AnalyticsApiResponse>(
          `${this.apiBase}/market/analytics/${mint}`
        )
      );

      const analytics: TokenAnalytics = {};

      // Parse DexScreener data
      if (resp?.dexscreener) {
        const pairs: DexScreenerPair[] = Array.isArray(resp.dexscreener)
          ? resp.dexscreener
          : resp.dexscreener?.pairs ?? [];
        const solanaPairs = pairs.filter((p) => p.chainId === 'solana');
        analytics.pairs = solanaPairs;

        // Find best pair by liquidity
        if (solanaPairs.length > 0) {
          const best = solanaPairs.reduce((a, b) =>
            (a.liquidity?.usd ?? 0) > (b.liquidity?.usd ?? 0) ? a : b
          );
          analytics.bestPair = {
            priceUsd: parseFloat(best.priceUsd ?? '0'),
            priceChange24h: best.priceChange?.h24 ?? 0,
            volume24h: best.volume?.h24 ?? 0,
            liquidity: best.liquidity?.usd ?? 0,
            marketCap: best.marketCap ?? 0,
            fdv: best.fdv ?? 0,
            pairAddress: best.pairAddress,
            dexId: best.dexId,
            txns24h: best.txns?.h24 ?? { buys: 0, sells: 0 },
            imageUrl: best.info?.imageUrl,
          };
        }
      }

      // Parse Birdeye data
      if (resp?.birdeye) {
        const overview: BirdeyeOverview = resp.birdeye?.data ?? resp.birdeye;
        analytics.overview = {
          address: overview.address,
          symbol: overview.symbol,
          name: overview.name,
          decimals: overview.decimals,
          price: overview.price,
          priceChange24h: overview.priceChange24hPercent,
          volume24h: overview.v24hUSD,
          marketCap: overview.mc,
          fdv: overview.fdv,
          liquidity: overview.liquidity,
          holder: overview.holder,
          supply: overview.supply,
          circulatingSupply: overview.circulatingSupply,
          trade24h: overview.trade24h,
          trade24hChangePercent: overview.trade24hChangePercent,
          buy24h: overview.buy24h,
          sell24h: overview.sell24h,
          uniqueWallet24h: overview.uniqueWallet24h,
          logoURI: overview.logoURI,
          description: overview.description,
          websites: overview.extensions?.website ? [overview.extensions.website] : [],
          socials: [],
        };
      }

      this.cache.set(mint, analytics);
      return analytics;
    } catch (err) {
      console.error('Analytics fetch error:', err);
      return {};
    }
  }

  /** Clear cache for a specific mint. */
  clearCache(mint: string): void {
    this.cache.delete(mint);
  }
}

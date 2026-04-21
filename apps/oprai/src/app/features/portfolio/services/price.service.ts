import { Injectable, inject } from '@angular/core';
import { BirdeyeService } from './birdeye.service';
import type { JupiterToken } from '../models/portfolio.models';

const TOKEN_LIST_URL = 'https://token.jup.ag/strict';
const SOL_MINT = 'So11111111111111111111111111111111111111112';

@Injectable({ providedIn: 'root' })
export class PriceService {
  private readonly birdeye = inject(BirdeyeService);
  private tokenListCache: Map<string, JupiterToken> | null = null;

  /**
   * Jupiter strict token list — metadata only (symbol, name, logo, decimals).
   * No auth required for this endpoint.
   */
  async getTokenList(): Promise<Map<string, JupiterToken>> {
    if (this.tokenListCache) return this.tokenListCache;

    try {
      const response = await fetch(TOKEN_LIST_URL);
      if (!response.ok) return new Map();
      const tokens: JupiterToken[] = await response.json();

      this.tokenListCache = new Map();
      for (const token of tokens) {
        this.tokenListCache.set(token.address, token);
      }

      return this.tokenListCache;
    } catch {
      return new Map();
    }
  }

  /**
   * USD prices via Birdeye (replaces Jupiter Price v2 which now requires auth).
   */
  async getPrices(mints: string[]): Promise<Map<string, number>> {
    return this.birdeye.getPrices(mints);
  }

  async getSolPrice(): Promise<number | null> {
    const prices = await this.getPrices([SOL_MINT]);
    return prices.get(SOL_MINT) ?? null;
  }

  /**
   * Fetch metadata for individual tokens not in the strict list
   * (meme coins, community tokens, etc.) from Jupiter's per-token API.
   */
  async getTokensMetadata(mints: string[]): Promise<Map<string, JupiterToken>> {
    const results = new Map<string, JupiterToken>();
    if (mints.length === 0) return results;

    const fetches = mints.map(async (mint) => {
      try {
        const response = await fetch(`https://tokens.jup.ag/token/${mint}`);
        if (!response.ok) return;
        const data = await response.json() as any;
        if (!data?.address) return;
        const token: JupiterToken = {
          address: data.address,
          symbol: data.symbol ?? mint.slice(0, 4),
          name: data.name ?? 'Unknown Token',
          decimals: data.decimals ?? 0,
          logoURI: data.logoURI ?? null,
        };
        results.set(mint, token);
        // Also update strict list cache for future lookups
        if (this.tokenListCache) {
          this.tokenListCache.set(mint, token);
        }
      } catch {
        // Skip — no metadata for this token
      }
    });

    await Promise.all(fetches);
    return results;
  }
}

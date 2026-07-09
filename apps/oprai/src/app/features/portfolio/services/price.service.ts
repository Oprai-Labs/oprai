import { Injectable, inject } from '@angular/core';
import { BirdeyeService } from './birdeye.service';
import { ChartDataService } from '@core/services/market/chart-data.service';
import type { JupiterToken } from '../models/portfolio.models';

const TOKEN_LIST_URL = 'https://token.jup.ag/strict';
const SOL_MINT = 'So11111111111111111111111111111111111111112';

@Injectable({ providedIn: 'root' })
export class PriceService {
  private readonly birdeye = inject(BirdeyeService);
  private readonly chart = inject(ChartDataService);
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

  // ──── 7-day price history (CoinGecko, free, no auth) ────
  // Cache the last fetched series for 5 minutes — the chart is purely
  // visual and price points 5 min apart move imperceptibly.
  private historyCache: { data: Array<{ t: number; price: number }>; ts: number } | null = null;
  private readonly HISTORY_TTL = 5 * 60 * 1000;

  /**
   * SOL price history for the last 7 days. Returns hourly samples sorted
   * ascending. Used to drive the portfolio sparkline — we approximate
   * portfolio value at each point as `solBalance × solPrice(t) + stableUsd`,
   * which is exact for SOL and constant for stables (the 7-day approx
   * ignores deposits/withdrawals, which is acceptable for a visual trend).
   */
  async getSolPriceHistory7d(): Promise<Array<{ t: number; price: number }>> {
    const now = Date.now();
    if (this.historyCache && now - this.historyCache.ts < this.HISTORY_TTL) {
      return this.historyCache.data;
    }
    try {
      const url =
        'https://api.coingecko.com/api/v3/coins/solana/market_chart?vs_currency=usd&days=7&interval=hourly';
      const res = await fetch(url);
      if (!res.ok) return this.historyCache?.data ?? [];
      const json = await res.json() as { prices?: Array<[number, number]> };
      const points = (json.prices ?? []).map(([t, price]) => ({ t, price }));
      this.historyCache = { data: points, ts: now };
      return points;
    } catch {
      return this.historyCache?.data ?? [];
    }
  }

  /**
   * Real per-token portfolio value history for the last 7 days.
   *
   * For each holding we pull hourly OHLCV from Birdeye (via the existing
   * gateway proxy) and at each canonical timestamp compute
   *   Σ holding.balance × close_at_t.
   *
   * **Important caveat**: this assumes the user has held the *current*
   * balances throughout the window. Swaps / deposits / withdrawals in the
   * last 7 days are not reflected — we'd need to walk every transaction
   * and reconstruct historical balances per timestamp, which requires
   * either a paid wallet-history API (Birdeye Pro `/wallet/balance_chart`)
   * or a much heavier Helius-based reconstruction. For a visual sparkline,
   * "current basket × historical prices" is a far better trend indicator
   * than the previous SOL-price-only proxy.
   *
   * Tokens whose Birdeye OHLCV fails (memes outside the indexer, fresh
   * Pump.fun launches) contribute their *current* USD value as a flat
   * baseline at every timestamp — better than dropping them entirely
   * (which would make the sparkline jump up at "now").
   */
  async getPortfolioHistory7d(
    holdings: Array<{ mint: string; balance: number; currentUsdValue: number }>,
  ): Promise<Array<{ t: number; value: number }>> {
    if (!holdings.length) return [];

    const now = Math.floor(Date.now() / 1000);
    const from = now - 7 * 86400;

    const series = await Promise.all(
      holdings.map(async (h) => {
        try {
          const candles = await this.chart.getOHLCV(h.mint, '1H', from, now);
          return { ...h, candles, hasHistory: candles.length > 0 };
        } catch {
          return { ...h, candles: [], hasHistory: false };
        }
      }),
    );

    // Use SOL's candles as the canonical timeline (always present in any
    // active wallet). Fall back to whichever priced series came back with
    // the most candles if SOL is missing for some reason.
    let canonical = series.find(
      (s) => s.mint === SOL_MINT && s.candles.length > 0,
    );
    if (!canonical) {
      canonical = series.reduce<typeof series[number] | undefined>(
        (best, s) =>
          !best || s.candles.length > best.candles.length ? s : best,
        undefined,
      );
    }
    if (!canonical || !canonical.candles.length) return [];

    // Pre-compute per-series sorted candle times for nearest-prior lookup.
    // For tokens with no historical coverage, we add their current USD as
    // a flat baseline at every point.
    const flatBaseline = series
      .filter((s) => !s.hasHistory)
      .reduce((sum, s) => sum + s.currentUsdValue, 0);

    const result: Array<{ t: number; value: number }> = [];
    for (const ref of canonical.candles) {
      let total = flatBaseline;
      for (const s of series) {
        if (!s.hasHistory) continue;
        // Find nearest candle at or before ref.time. Linear scan is fine
        // for ~168 candles per token; binary search would micro-optimise.
        let close = 0;
        for (let i = s.candles.length - 1; i >= 0; i--) {
          if (s.candles[i].time <= ref.time) {
            close = s.candles[i].close;
            break;
          }
        }
        total += s.balance * close;
      }
      result.push({ t: ref.time * 1000, value: total });
    }
    return result;
  }
}

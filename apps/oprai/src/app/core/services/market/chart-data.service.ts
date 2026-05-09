import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { CacheStore } from '../../utils/cache-store';

export type ChartInterval = '1s' | '1m' | '3m' | '5m' | '15m' | '1H' | '4H' | '1D' | '1W';

export interface OHLCVCandle {
  time: number; // unix timestamp in seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

@Injectable({ providedIn: 'root' })
export class ChartDataService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBase;
  private readonly cache = new CacheStore<OHLCVCandle[]>(60_000);

  /** Fetch OHLCV candle data from Birdeye via gateway proxy. */
  async getOHLCV(
    mint: string,
    interval: ChartInterval = '15m',
    from?: number,
    to?: number
  ): Promise<OHLCVCandle[]> {
    const now = Math.floor(Date.now() / 1000);
    const timeFrom = from ?? now - this.getDefaultRange(interval);
    const timeTo = to ?? now;

    const cacheKey = `${mint}:${interval}:${timeFrom}:${timeTo}`;
    const cached = this.cache.get(cacheKey);
    if (cached) return cached;

    try {
      const params = new URLSearchParams({
        address: mint,
        type: interval,
        time_from: String(timeFrom),
        time_to: String(timeTo),
      });

      const resp = await firstValueFrom(
        this.http.get<any>(`${this.apiBase}/market/ohlcv?${params}`)
      );

      const items = resp?.data?.items ?? resp?.items ?? [];
      const candles: OHLCVCandle[] = items.map((item: any) => ({
        time: item.unixTime ?? item.time,
        open: item.o ?? item.open,
        high: item.h ?? item.high,
        low: item.l ?? item.low,
        close: item.c ?? item.close,
        volume: item.v ?? item.volume ?? 0,
      }));

      // Sort by time ascending
      candles.sort((a, b) => a.time - b.time);

      this.cache.set(cacheKey, candles);
      return candles;
    } catch (err) {
      console.error('OHLCV fetch error:', err);
      return [];
    }
  }

  /** Get default time range for an interval. */
  private getDefaultRange(interval: ChartInterval): number {
    switch (interval) {
      case '1s': return 600; // 10 minutes
      case '1m': return 3600; // 1 hour
      case '3m': return 3 * 3600; // 3 hours
      case '5m': return 6 * 3600; // 6 hours
      case '15m': return 24 * 3600; // 1 day
      case '1H': return 7 * 86400; // 1 week
      case '4H': return 30 * 86400; // 1 month
      case '1D': return 90 * 86400; // 3 months
      case '1W': return 365 * 86400; // 1 year
    }
  }

  /** Convert ChartTimeframe string to ChartInterval. */
  timeframeToInterval(tf: string): ChartInterval {
    switch (tf) {
      case '1H': return '1m';
      case '4H': return '5m';
      case '1D': return '15m';
      case '1W': return '1H';
      case '1M': return '4H';
      default: return '15m';
    }
  }

  /** Get time range for a timeframe. */
  timeframeToRange(tf: string): number {
    switch (tf) {
      case '1H': return 3600;
      case '4H': return 4 * 3600;
      case '1D': return 86400;
      case '1W': return 7 * 86400;
      case '1M': return 30 * 86400;
      default: return 86400;
    }
  }
}

import { Injectable, inject, NgZone } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { CacheStore } from '../../utils/cache-store';

export interface Trade {
  txHash: string;
  blockUnixTime: number;
  side: 'buy' | 'sell';
  source: string;
  tokenAmount: number;
  tokenSymbol: string;
  tokenAddress: string;
  quoteAmount: number;
  quoteSymbol: string;
  priceUsd: number;
  volumeUsd: number;
  owner: string;
}

@Injectable({ providedIn: 'root' })
export class TokenTradesService {
  private readonly http = inject(HttpClient);
  private readonly zone = inject(NgZone);
  private readonly apiBase = environment.apiBase;
  private readonly cache = new CacheStore<Trade[]>(15_000);

  async getRecentTrades(mint: string): Promise<Trade[]> {
    const cacheKey = `trades:${mint}`;
    const cached = this.cache.get(cacheKey);
    if (cached) return cached;

    try {
      interface RawTradeItem {
        txHash?: string; signature?: string; blockUnixTime?: number; timestamp?: number;
        side?: string; type?: string; source?: string; dex?: string;
        tokenAmount?: number; amount?: number; tokenSymbol?: string; tokenAddress?: string;
        quoteAmount?: number; solAmount?: number; quoteSymbol?: string;
        priceUsd?: number; price?: number; volumeUsd?: number; usdValue?: number;
        owner?: string; wallet?: string;
      }
      interface RawTradesResp { data?: { items?: RawTradeItem[] }; items?: RawTradeItem[] }
      const resp = await firstValueFrom(
        this.http.get<RawTradesResp>(`${this.apiBase}/market/trades/${mint}`)
      );
      const items: RawTradeItem[] = resp?.data?.items ?? resp?.items ?? [];
      const trades: Trade[] = items.map((t) => ({
        txHash: t.txHash ?? t.signature ?? '',
        blockUnixTime: t.blockUnixTime ?? t.timestamp ?? 0,
        side: (t.side ?? t.type ?? 'buy').toLowerCase() as 'buy' | 'sell',
        source: t.source ?? t.dex ?? '',
        tokenAmount: t.tokenAmount ?? t.amount ?? 0,
        tokenSymbol: t.tokenSymbol ?? '',
        tokenAddress: t.tokenAddress ?? mint,
        quoteAmount: t.quoteAmount ?? t.solAmount ?? 0,
        quoteSymbol: t.quoteSymbol ?? 'SOL',
        priceUsd: t.priceUsd ?? t.price ?? 0,
        volumeUsd: t.volumeUsd ?? t.usdValue ?? 0,
        owner: t.owner ?? t.wallet ?? '',
      }));
      this.cache.set(cacheKey, trades);
      return trades;
    } catch (err) {
      console.error('Trades fetch error:', err);
      return [];
    }
  }

  pollTrades(mint: string, intervalMs = 10_000): Observable<Trade[]> {
    return new Observable<Trade[]>(subscriber => {
      let active = true;

      const poll = async () => {
        if (!active) return;
        if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
        try {
          const trades = await this.getRecentTrades(mint);
          this.zone.run(() => subscriber.next(trades));
        } catch { /* swallow */ }
      };

      poll();
      const id = setInterval(poll, intervalMs);

      return () => {
        active = false;
        clearInterval(id);
      };
    });
  }
}

import { Injectable, inject, NgZone } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, Subject } from 'rxjs';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { CacheStore } from '../../utils/cache-store';

export interface PriceData {
  id: string;
  price: number;
  extraInfo?: {
    lastSwappedPrice?: {
      lastJupiterSellAt?: number;
      lastJupiterSellPrice?: string;
      lastJupiterBuyAt?: number;
      lastJupiterBuyPrice?: string;
    };
    quotedPrice?: {
      buyPrice?: string;
      buyAt?: number;
      sellPrice?: string;
      sellAt?: number;
    };
    confidenceLevel?: string;
  };
}

export interface PriceMap {
  [mint: string]: PriceData;
}

@Injectable({ providedIn: 'root' })
export class PriceFeedService {
  private readonly http = inject(HttpClient);
  private readonly zone = inject(NgZone);
  private readonly apiBase = environment.apiBase;

  private readonly cache = new CacheStore<PriceData>(15_000); // 15s default
  private readonly destroy$ = new Subject<void>();

  /** Fetch prices for a batch of token mints (up to 100). */
  async getPrices(mints: string[]): Promise<Map<string, PriceData>> {
    const result = new Map<string, PriceData>();
    if (!mints.length) return result;

    const toFetch: string[] = [];
    for (const mint of mints) {
      const cached = this.cache.get(mint);
      if (cached) {
        result.set(mint, cached);
      } else {
        toFetch.push(mint);
      }
    }

    if (!toFetch.length) return result;

    // Jupiter Price API v2 supports up to 100 IDs
    const batchSize = 100;
    for (let i = 0; i < toFetch.length; i += batchSize) {
      const batch = toFetch.slice(i, i + batchSize);
      try {
        const ids = batch.join(',');
        const resp = await firstValueFrom(
          this.http.get<Record<string, any>>(`${this.apiBase}/market/prices?ids=${ids}`)
        );
        // Jupiter Price API v3 returns mint keys at the TOP LEVEL with a
        // `usdPrice` field; v2 nested them under `data` with a `price` field.
        // Accept BOTH so a gateway upstream version bump never silently zeroes
        // every price (which hides USD values + the DCA/limit min-size guards).
        const priceMap: Record<string, any> =
          resp && typeof resp === 'object' && resp['data'] && typeof resp['data'] === 'object'
            ? resp['data']
            : (resp ?? {});
        for (const [mint, priceData] of Object.entries(priceMap)) {
          if (!priceData || typeof priceData !== 'object') continue;
          const price = Number((priceData as any).usdPrice ?? (priceData as any).price);
          if (Number.isFinite(price) && price > 0) {
            const pd: PriceData = {
              id: mint,
              price,
              extraInfo: (priceData as any).extraInfo,
            };
            this.cache.set(mint, pd);
            result.set(mint, pd);
          }
        }
      } catch (err) {
        console.error('Price fetch error for batch:', err);
      }
    }

    return result;
  }

  /** Get single token price. */
  async getPrice(mint: string): Promise<number | null> {
    const prices = await this.getPrices([mint]);
    return prices.get(mint)?.price ?? null;
  }

  /**
   * Watch prices for a set of mints with automatic polling.
   * Visibility-aware: pauses when tab is hidden.
   */
  watchPrices(mints: string[], intervalMs = 30_000): Observable<Map<string, PriceData>> {
    return new Observable<Map<string, PriceData>>((subscriber) => {
      let active = true;

      const poll = async () => {
        if (!active) return;
        try {
          const prices = await this.getPrices(mints);
          if (active) {
            this.zone.run(() => subscriber.next(prices));
          }
        } catch (err) {
          console.error('Price watch error:', err);
        }
      };

      // Initial fetch
      poll();

      // Set up interval polling, pause when hidden
      const intervalId = setInterval(() => {
        if (document.visibilityState === 'visible') {
          poll();
        }
      }, intervalMs);

      return () => {
        active = false;
        clearInterval(intervalId);
      };
    });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }
}

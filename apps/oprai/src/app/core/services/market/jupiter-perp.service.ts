/**
 * Jupiter Perpetuals Service
 *
 * Program: PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu
 * JLP Mint: 27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4
 * API: https://api.jup.ag/perp/v2
 *
 * Rate format: index_price, funding_rate as string floats.
 * PnL: string float (positive = profit, negative = loss).
 */
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../api.service';

const PERP_API = 'https://api.jup.ag/perp/v2';

export interface PerpMarket {
  symbol: string;       // "SOL", "wETH", "wBTC"
  indexPrice: number;   // USD
  fundingRate: number;  // hourly rate
  openInterest: number; // USD
}

export interface PerpPosition {
  market: string;           // "SOL", "wETH", "wBTC"
  side: 'long' | 'short';
  sizeUsd: number;          // position size in USD
  collateral: number;       // collateral in USD
  entryPrice: number;       // USD
  currentPrice: number;     // USD
  unrealizedPnl: number;    // USD
  liquidationPrice: number; // USD
  leverage: number;         // e.g. 5.0
  positionPubkey?: string;  // for closing/adjusting the position
  closed?: boolean;         // UI flag: user initiated a close; keep it shown as closed
}

@Injectable({ providedIn: 'root' })
export class JupiterPerpService {
  private readonly api = inject(ApiService);

  /** Fetch market data for all Jupiter Perp markets. */
  async getMarkets(): Promise<PerpMarket[]> {
    try {
      const res = await fetch(`${PERP_API}/markets`);
      if (!res.ok) return [];
      const data: any = await res.json();
      const markets: any[] = data.markets ?? data ?? [];
      return markets.map(m => ({
        symbol: m.symbol ?? '',
        indexPrice: parseFloat(m.indexPrice ?? m.index_price ?? '0'),
        fundingRate: parseFloat(m.fundingRate ?? m.funding_rate ?? '0'),
        openInterest: parseFloat(m.openInterest ?? m.open_interest ?? '0'),
      }));
    } catch {
      return [];
    }
  }

  /**
   * Fetch open perpetual positions for a wallet.
   *
   * Routed through the gateway (`/actions/build` → Rust `build_perp_positions`
   * → Jupiter perps-api v2) rather than a direct browser call: it keeps the
   * request authenticated, avoids CORS, and uses the real v2 endpoint. The v2
   * `dataList` reports USD amounts in micro-USD (1e6) and prices likewise, so
   * every USD field is scaled down here.
   */
  async getPositions(walletAddress: string): Promise<PerpPosition[]> {
    return (await this.getPositionsResult(walletAddress)).positions;
  }

  /**
   * Like getPositions but reports whether the fetch actually succeeded, so
   * callers can distinguish "no open positions" (ok, empty) from "couldn't
   * reach the API" (not ok). Reconciling a snapshot must NOT mark positions
   * closed on a transient failure — only on a confirmed-empty live result.
   */
  async getPositionsResult(walletAddress: string): Promise<{ ok: boolean; positions: PerpPosition[] }> {
    try {
      const res = await firstValueFrom(
        this.api.post<{ preview?: { params?: { dataList?: any[] } } }>('/actions/build', {
          type: 'perp_positions',
          params: {},
        }),
      );
      const list = res?.preview?.params?.dataList;
      if (!Array.isArray(list)) return { ok: false, positions: [] };
      const usd = (v: unknown) => parseFloat(String(v ?? '0')) / 1e6;

      const positions = list
        .filter(p => parseFloat(String(p.sizeUsd ?? '0')) > 0)
        .map(p => ({
          market: p.asset ?? p.market ?? '',
          side: (p.side ?? 'long') as 'long' | 'short',
          sizeUsd: usd(p.sizeUsd),
          collateral: usd(p.collateralUsd),
          entryPrice: usd(p.entryPriceUsd),
          currentPrice: usd(p.markPriceUsd),
          unrealizedPnl: usd(p.pnlAfterFeesUsd),
          liquidationPrice: usd(p.liquidationPriceUsd),
          leverage: parseFloat(String(p.leverage ?? '0')),
          positionPubkey: p.positionPubkey ?? '',
        }));
      return { ok: true, positions };
    } catch {
      return { ok: false, positions: [] };
    }
  }
}

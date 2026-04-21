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
import { Injectable } from '@angular/core';

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
}

@Injectable({ providedIn: 'root' })
export class JupiterPerpService {

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

  /** Fetch open perpetual positions for a wallet. */
  async getPositions(walletAddress: string): Promise<PerpPosition[]> {
    try {
      const res = await fetch(`${PERP_API}/positions?wallet=${walletAddress}`);
      if (!res.ok) return [];
      const data: any[] = await res.json();
      if (!Array.isArray(data)) return [];

      return data
        .filter(p => parseFloat(p.size ?? p.sizeUsd ?? '0') > 0)
        .map(p => ({
          market: p.market ?? p.symbol ?? '',
          side: (p.side ?? 'long') as 'long' | 'short',
          sizeUsd: parseFloat(p.sizeUsd ?? p.size ?? '0'),
          collateral: parseFloat(p.collateral ?? p.collateralUsd ?? '0'),
          entryPrice: parseFloat(p.entryPrice ?? p.entry_price ?? '0'),
          currentPrice: parseFloat(p.currentPrice ?? p.current_price ?? p.markPrice ?? '0'),
          unrealizedPnl: parseFloat(p.unrealizedPnl ?? p.pnl ?? p.unrealized_pnl ?? '0'),
          liquidationPrice: parseFloat(p.liquidationPrice ?? p.liquidation_price ?? '0'),
          leverage: parseFloat(p.leverage ?? '0'),
        }));
    } catch {
      return [];
    }
  }
}

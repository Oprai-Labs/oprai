import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from '../../../core/services/api.service';

export interface EvmToken {
  chain: string;
  network: string;
  address: string;
  symbol: string;
  name: string;
  decimals: number;
  logo?: string;
  uiAmount: number;
  priceUsd: number;
  valueUsd: number;
  native: boolean;
}

export interface EvmPortfolio {
  address: string;
  totalUsd: number;
  tokens: EvmToken[];
}

/**
 * EVM wallet holdings via the gateway (backed by Alchemy's multichain Data API).
 * One call per linked EVM wallet returns balances + USD across ETH, Base,
 * Arbitrum, Optimism and Polygon.
 */
@Injectable({ providedIn: 'root' })
export class EvmPortfolioService {
  private readonly api = inject(ApiService);

  getPortfolio(address: string): Observable<EvmPortfolio> {
    return this.api.get<EvmPortfolio>(`/market/evm/portfolio?address=${encodeURIComponent(address)}`);
  }
}

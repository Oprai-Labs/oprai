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

export interface EvmPositionToken {
  symbol: string;
  type: string;
  amount: number;
  logo?: string;
}

export interface EvmPosition {
  chain: string;
  protocol: string;
  protocolId: string;
  protocolUrl?: string;
  logo?: string;
  label: string;
  balanceUsd: number;
  unclaimedUsd: number;
  tokens: EvmPositionToken[];
}

export interface EvmPositions {
  address: string;
  totalUsd: number;
  positions: EvmPosition[];
}

export interface EvmTx {
  hash: string;
  chain: string;
  timestamp: string;
  category: string;
  summary: string;
  platform?: string;
  platformLogo?: string;
  direction: string;
  success: boolean;
}

export interface EvmTransactions {
  address: string;
  transactions: EvmTx[];
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

  /** Protocol-labeled DeFi positions (Aave, Uniswap, Lido, …) across chains. */
  getPositions(address: string): Observable<EvmPositions> {
    return this.api.get<EvmPositions>(`/market/evm/positions?address=${encodeURIComponent(address)}`);
  }

  /** Recent transactions, platform-labeled (which app each tx touched), across chains. */
  getTransactions(address: string): Observable<EvmTransactions> {
    return this.api.get<EvmTransactions>(`/market/evm/transactions?address=${encodeURIComponent(address)}`);
  }
}

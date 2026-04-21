/**
 * TransactionPreviewService
 *
 * Previews transaction outcomes before signing: balance changes,
 * risk assessment, and protocol-specific details.
 */
import { Injectable, inject } from '@angular/core';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { ParsedAction } from './intent-parser.service';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface BalanceChange {
  mint: string;
  symbol: string;
  decimals: number;
  amount: number;
  direction: 'in' | 'out';
  usdValue: number;
  icon?: string;
  logoUri?: string;
  balanceBefore: number;
  balanceAfter: number;
}

export interface RiskAssessment {
  level: 'low' | 'medium' | 'high';
  factors: string[];
  warnings: string[];
}

export interface TransactionPreview {
  action: ParsedAction;
  actionType: string;
  description: string;
  balanceChanges: BalanceChange[];
  risk: RiskAssessment;
  estimatedFee: number;
  networkFeeSol: number;
  networkFeeUsd: number;
  protocol: string;
  route?: string[];
  priceImpact?: number;
  priceImpactPercent: number;
  summary: string;
  timestamp: number;
  canExecute: boolean;
  simulationError?: string;
  warnings: string[];
}

// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class TransactionPreviewService {
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly priceFeed = inject(PriceFeedService);

  /**
   * Generate a transaction preview for the given action.
   */
  async preview(action: ParsedAction): Promise<TransactionPreview> {
    const wallet = ''; // wallet address would be injected in production
    switch (action.type) {
      case 'swap':
        return this.previewSwap(action);
      case 'transfer':
        return this.previewTransfer(action);
      case 'stake':
      case 'unstake':
        return this.previewStake(action);
      case 'lend':
      case 'withdraw':
        return this.previewLend(action);
      case 'borrow':
      case 'repay':
        return this.previewBorrow(action);
      case 'add_liquidity':
      case 'remove_liquidity':
        return this.previewLiquidity(action);
      default:
        return this.previewGeneric(action);
    }
  }

  /**
   * Format a balance change for display.
   */
  formatChange(change: BalanceChange): string {
    const sign = change.direction === 'in' ? '+' : '-';
    const amount = Math.abs(change.amount);
    return `${sign}${amount.toFixed(change.decimals > 6 ? 4 : 2)} ${change.symbol}`;
  }

  /**
   * Format a USD value for display.
   */
  formatUsd(value: number): string {
    if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
    if (value >= 1_000) return `$${(value / 1_000).toFixed(2)}K`;
    return `$${value.toFixed(2)}`;
  }

  // ── Private preview methods ────────────────────────────────────────────────

  private async previewSwap(action: ParsedAction): Promise<TransactionPreview> {
    return this.buildPreview(action, 'Jupiter');
  }

  private async previewTransfer(action: ParsedAction): Promise<TransactionPreview> {
    return this.buildPreview(action, 'System');
  }

  private async previewStake(action: ParsedAction): Promise<TransactionPreview> {
    return this.buildPreview(action, action.params['protocol'] ?? 'Jito');
  }

  private async previewLend(action: ParsedAction): Promise<TransactionPreview> {
    return this.buildPreview(action, action.params['protocol'] ?? 'Jupiter');
  }

  private async previewBorrow(action: ParsedAction): Promise<TransactionPreview> {
    return this.buildPreview(action, action.params['protocol'] ?? 'Jupiter');
  }

  private async previewLiquidity(action: ParsedAction): Promise<TransactionPreview> {
    return this.buildPreview(action, action.params['protocol'] ?? 'Orca');
  }

  private async previewGeneric(action: ParsedAction): Promise<TransactionPreview> {
    return this.buildPreview(action, 'Unknown');
  }

  private buildPreview(action: ParsedAction, protocol: string): TransactionPreview {
    return {
      action,
      actionType: action.type,
      description: `${action.type} ${action.params['amount'] ?? ''} ${action.params['token'] ?? ''}`.trim(),
      balanceChanges: [],
      risk: { level: 'low', factors: [], warnings: [] },
      estimatedFee: 0.000005,
      networkFeeSol: 0.000005,
      networkFeeUsd: 0.001,
      protocol,
      summary: `${action.type} ${action.params['amount'] ?? ''} ${action.params['token'] ?? ''}`.trim(),
      timestamp: Date.now(),
      canExecute: true,
      priceImpactPercent: 0,
      warnings: [],
    };
  }
}

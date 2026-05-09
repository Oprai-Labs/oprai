/**
 * TransactionPreviewService
 *
 * Previews transaction outcomes before signing: balance changes,
 * risk assessment, and protocol-specific details.
 */
import { Injectable, inject } from '@angular/core';
import { firstValueFrom, timeout } from 'rxjs';
import { TokenRegistryService } from '@core/services/market/token-registry.service';
import { PriceFeedService } from '@core/services/market/price-feed.service';
import { ApiService } from '@core/services/api.service';
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

/** One hop in a Jupiter swap route. */
export interface RouteHop {
  /** Human-friendly DEX name, e.g. "Orca (Whirlpool)". */
  dex: string;
  /** Symbol (or shortened mint) for the input side of this hop. */
  inputSymbol: string;
  outputSymbol: string;
  /** Percentage of the order routed through this hop (0–100). */
  percent: number;
  /** Fee in native token units (already divided by decimals). */
  feeAmount?: number;
  feeMint?: string;
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
  /** Multi-hop route for swaps. Empty/undefined for simple transfers. */
  route?: RouteHop[];
  priceImpact?: number;
  priceImpactPercent: number;
  summary: string;
  timestamp: number;
  canExecute: boolean;
  simulationError?: string;
  warnings: string[];
}

interface JupiterQuoteResponse {
  inputMint: string;
  inAmount: string;
  outputMint: string;
  outAmount: string;
  priceImpactPct?: string;
  routePlan?: Array<{
    swapInfo: {
      ammKey: string;
      label?: string;
      inputMint: string;
      outputMint: string;
      inAmount: string;
      outAmount: string;
      feeAmount?: string;
      feeMint?: string;
    };
    percent: number;
  }>;
}

// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class TransactionPreviewService {
  private readonly tokenRegistry = inject(TokenRegistryService);
  private readonly priceFeed = inject(PriceFeedService);
  private readonly api = inject(ApiService);

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
    const base = this.buildPreview(action, 'Jupiter');
    // Resolve mints from the action params; fall back to symbol lookup.
    const inputMint = this.resolveMint(action.params['inputMint'] ?? action.params['fromToken'] ?? action.params['token']);
    const outputMint = this.resolveMint(action.params['outputMint'] ?? action.params['toToken']);
    const amountStr = action.params['amount'] ?? action.params['inputAmount'];
    if (!inputMint || !outputMint || !amountStr) return base;

    // Convert UI amount → atomic. Token decimals come from the registry; if
    // unknown, assume 9 (SOL) which is the most common case here.
    const inToken = this.tokenRegistry.getToken(inputMint);
    const outToken = this.tokenRegistry.getToken(outputMint);
    const inDecimals = inToken?.decimals ?? 9;
    const outDecimals = outToken?.decimals ?? 9;
    const inAmountAtomic = Math.floor(parseFloat(amountStr) * Math.pow(10, inDecimals));
    if (!Number.isFinite(inAmountAtomic) || inAmountAtomic <= 0) return base;

    try {
      const quote = await firstValueFrom(
        this.api.post<{ quote: JupiterQuoteResponse } | JupiterQuoteResponse>('/actions/quote', {
          inputMint,
          outputMint,
          amount: String(inAmountAtomic),
          slippageBps: 50,
        }).pipe(timeout(8_000))
      );
      const q: JupiterQuoteResponse = (quote as any).quote ?? (quote as any);
      const routePlan = q.routePlan ?? [];
      const route: RouteHop[] = routePlan.map((hop) => {
        const swap = hop.swapInfo;
        const feeMintToken = this.tokenRegistry.getToken(swap.feeMint ?? '');
        const feeDec = feeMintToken?.decimals ?? 9;
        const fee = swap.feeAmount ? parseFloat(swap.feeAmount) / Math.pow(10, feeDec) : undefined;
        return {
          dex: swap.label ?? 'Unknown DEX',
          inputSymbol: this.tokenRegistry.getToken(swap.inputMint)?.symbol ?? this.shortMint(swap.inputMint),
          outputSymbol: this.tokenRegistry.getToken(swap.outputMint)?.symbol ?? this.shortMint(swap.outputMint),
          percent: hop.percent,
          feeAmount: fee,
          feeMint: feeMintToken?.symbol ?? swap.feeMint,
        };
      });

      const inAmountUi = parseFloat(q.inAmount) / Math.pow(10, inDecimals);
      const outAmountUi = parseFloat(q.outAmount) / Math.pow(10, outDecimals);
      const priceImpact = q.priceImpactPct ? parseFloat(q.priceImpactPct) * 100 : 0;

      base.route = route;
      base.priceImpact = priceImpact;
      base.priceImpactPercent = priceImpact;
      base.balanceChanges = [
        {
          mint: inputMint,
          symbol: inToken?.symbol ?? this.shortMint(inputMint),
          decimals: inDecimals,
          amount: -inAmountUi,
          direction: 'out',
          usdValue: 0,
          logoUri: inToken?.logoURI ?? undefined,
          balanceBefore: 0,
          balanceAfter: 0,
        },
        {
          mint: outputMint,
          symbol: outToken?.symbol ?? this.shortMint(outputMint),
          decimals: outDecimals,
          amount: outAmountUi,
          direction: 'in',
          usdValue: 0,
          logoUri: outToken?.logoURI ?? undefined,
          balanceBefore: 0,
          balanceAfter: 0,
        },
      ];
      if (priceImpact > 3) {
        base.warnings.push(`Price impact ${priceImpact.toFixed(2)}% — large vs. pool depth.`);
        base.risk.level = 'high';
      } else if (priceImpact > 1) {
        base.risk.level = 'medium';
      }
    } catch {
      // Quote failed — return the skeleton preview with no route info.
    }
    return base;
  }

  /** Best-effort mint resolution from a symbol or address. */
  private resolveMint(value: string | undefined): string | null {
    if (!value) return null;
    if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(value)) return value;
    return this.tokenRegistry.getBySymbol(value)?.address ?? null;
  }

  private shortMint(mint: string): string {
    return mint && mint.length > 8 ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : (mint ?? '?');
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

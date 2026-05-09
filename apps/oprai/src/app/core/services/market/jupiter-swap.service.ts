import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

export interface SwapQuote {
  inputMint: string;
  inAmount: string;
  outputMint: string;
  outAmount: string;
  otherAmountThreshold: string;
  swapMode: string;
  slippageBps: number;
  priceImpactPct: string;
  routePlan: RoutePlanStep[];
  contextSlot?: number;
}

export interface RoutePlanStep {
  swapInfo: {
    ammKey: string;
    label?: string;
    inputMint: string;
    outputMint: string;
    inAmount: string;
    outAmount: string;
    feeAmount: string;
    feeMint: string;
  };
  percent: number;
}

export interface SwapTransaction {
  swapTransaction: string; // base64 encoded transaction
  lastValidBlockHeight: number;
}

@Injectable({ providedIn: 'root' })
export class JupiterSwapService {
  private readonly http = inject(HttpClient);

  /**
   * Get a swap quote via the OPRAI gateway (paid Jupiter developer API).
   * swapMode: 'ExactIn' (default) | 'ExactOut'
   */
  async getQuote(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps = 50,
    swapMode: 'ExactIn' | 'ExactOut' = 'ExactIn'
  ): Promise<SwapQuote | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<{ quote: SwapQuote }>(`${environment.apiBase}/actions/quote`, {
          input_mint: inputMint,
          output_mint: outputMint,
          amount,
          slippage_bps: slippageBps,
          swap_mode: swapMode,
        })
      );
      return resp?.quote ?? null;
    } catch (err) {
      console.error('Jupiter quote error:', err);
      return null;
    }
  }

  /** Get route labels from a quote for display. */
  getRouteLabels(quote: SwapQuote): string[] {
    if (!quote?.routePlan) return [];
    return quote.routePlan
      .map(step => step.swapInfo.label || 'Unknown DEX')
      .filter((label, idx, arr) => arr.indexOf(label) === idx);
  }

  /** Calculate price impact as a number. */
  getPriceImpact(quote: SwapQuote): number {
    return parseFloat(quote.priceImpactPct || '0');
  }

  /** Calculate exchange rate from quote. */
  getExchangeRate(quote: SwapQuote, inputDecimals: number, outputDecimals: number): number {
    const inAmt = parseInt(quote.inAmount) / Math.pow(10, inputDecimals);
    const outAmt = parseInt(quote.outAmount) / Math.pow(10, outputDecimals);
    if (inAmt === 0) return 0;
    return outAmt / inAmt;
  }
}

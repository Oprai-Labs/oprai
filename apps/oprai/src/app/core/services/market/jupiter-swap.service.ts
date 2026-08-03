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
  /** OPRAI's cut, as Jupiter priced it into this quote. Present only when the
   *  backend declared a fee it can actually collect, so the card can show the
   *  real number for this trade rather than a rule copied from the server. */
  platformFee?: { amount?: string; feeBps?: number } | null;
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
    swapMode: 'ExactIn' | 'ExactOut' = 'ExactIn',
    /** Restrict routing to one venue, e.g. 'Whirlpool' for an Orca swap, so
     *  the preview is priced through the DEX the action actually uses. */
    dexes?: string,
  ): Promise<SwapQuote | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<{ quote: SwapQuote }>(`${environment.apiBase}/actions/quote`, {
          input_mint: inputMint,
          output_mint: outputMint,
          amount,
          slippage_bps: slippageBps,
          swap_mode: swapMode,
          ...(dexes ? { dexes } : {}),
        })
      );
      return resp?.quote ?? null;
    } catch (err) {
      console.error('Jupiter quote error:', err);
      return null;
    }
  }

  /**
   * Live price estimate for a Raydium swap, quoted from Raydium's OWN compute
   * endpoint (same venue that executes) so the preview never shows a
   * foreign-DEX price. Uses the existing `raydium_swap_quote` build action
   * (gateway → solana-service → Raydium transaction-v1 /compute), which returns
   * the raw Raydium compute payload under preview.params.data. We reshape it
   * into the Jupiter-style SwapQuote the swap widget already consumes.
   */
  async getRaydiumQuote(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps = 50,
    swapMode: 'ExactIn' | 'ExactOut' = 'ExactIn'
  ): Promise<SwapQuote | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<{ preview?: { params?: { data?: Record<string, unknown> } } }>(
          `${environment.apiBase}/actions/build`,
          {
            type: 'raydium_swap_quote',
            params: {
              inputMint,
              outputMint,
              amount,
              slippageBps,
              swapMode: swapMode === 'ExactOut' ? 'out' : 'in',
            },
          },
        )
      );
      const d = resp?.preview?.params?.data as Record<string, unknown> | undefined;
      if (!d || d['outputAmount'] == null || d['inputAmount'] == null) return null;
      // Raydium returns priceImpactPct as a PERCENT number (0.12 = 0.12%); the
      // swap widget multiplies by 100 (Jupiter convention: fraction string), so
      // divide back to a fraction to keep one contract on the consumer side.
      const impactPct = Number(d['priceImpactPct'] ?? 0);
      return {
        inputMint: String(d['inputMint'] ?? inputMint),
        inAmount: String(d['inputAmount']),
        outputMint: String(d['outputMint'] ?? outputMint),
        outAmount: String(d['outputAmount']),
        otherAmountThreshold: String(d['otherAmountThreshold'] ?? '0'),
        swapMode: swapMode === 'ExactOut' ? 'ExactOut' : 'ExactIn',
        slippageBps,
        priceImpactPct: String((Number.isFinite(impactPct) ? impactPct : 0) / 100),
        routePlan: [],
      };
    } catch (err) {
      console.error('Raydium quote error:', err);
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

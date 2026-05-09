import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { ApiService } from '../api.service';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Marinade stats response */
export interface MarinadeStats {
  msolPrice: string;
  totalSolStaked: string;
  apy: number;
  tvl: number;
}

/** Marinade validator info */
export interface MarinadeValidator {
  voteAccount: string;
  name: string;
  stake: string;
  apy: number;
  commission: number;
}

/** Build response from backend */
export interface MarinadeBuildResponse {
  preview: {
    id: string;
    type: string;
    description: string;
    estimatedFee: string;
    params: Record<string, unknown>;
    warnings: string[];
    requiresApproval: boolean;
  };
  transaction?: string;
}

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const MARINADE_API = 'https://api.marinade.finance';

// Marinade Finance Program ID
export const MARINADE_PROGRAM_ID = 'MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD';

// mSOL token mint
export const MSOL_MINT = 'mSoLzYCx24dS9o7u4BMfKb5QZ6eE6a7pE2d7Y8Z9w0x1';

// Marinade State Account
export const MARINADE_STATE = '8szGkuLTAux9XMgZ2vtY39jVSowEcpBfFfD8hXSEqdGC';

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class MarinadeService {
  private readonly http = inject(HttpClient);
  private readonly api = inject(ApiService);

  // ─── Stats Queries ────────────────────────────────────────────────────────────

  /** Get Marinade stats (mSOL APY, price, TVL). */
  async getStats(): Promise<MarinadeStats | null> {
    try {
      // GET /msol/apy/1d returns { value, end_time, end_price, start_time, start_price }
      const apyResp = await firstValueFrom(
        this.http.get<any>(`${MARINADE_API}/msol/apy/1d`)
      );
      if (!apyResp) return null;
      return {
        msolPrice: String(apyResp.end_price ?? 1),
        totalSolStaked: '0',
        apy: parseFloat(apyResp.value ?? '0') * 100,  // convert to %
        tvl: 0,
      };
    } catch (err) {
      console.error('Failed to fetch Marinade stats:', err);
      return null;
    }
  }

  /**
   * Get the current mSOL/SOL exchange rate.
   *
   * Uses the gateway-proxied `/market/marinade/exchange-rate` route which hits
   * `https://mainnet-assets.marinade.finance/v1/stats` — the live spot price
   * Marinade's own staking dashboard polls (sub-block freshness from their
   * indexer). The previous `/msol/apy/1d` endpoint returned an end-of-window
   * snapshot up to a day stale, which is wrong for an "expected receive"
   * preview shown next to the user's input.
   *
   * Returns null on upstream failure — callers must hide the preview rather
   * than fall back to a static constant.
   */
  async getExchangeRate(): Promise<number | null> {
    try {
      const resp = await firstValueFrom(
        this.api.get<{ msolPrice?: string | number }>('/market/marinade/exchange-rate')
      );
      const raw = resp?.msolPrice;
      const v = typeof raw === 'string' ? parseFloat(raw) : (typeof raw === 'number' ? raw : NaN);
      return Number.isFinite(v) && v > 0 ? v : null;
    } catch {
      return null;
    }
  }

  /** Get list of validators. */
  async getValidators(): Promise<MarinadeValidator[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<{ validators: MarinadeValidator[] }>(`${MARINADE_API}/v1/validators`)
      );
      return resp?.validators ?? [];
    } catch (err) {
      console.error('Failed to fetch Marinade validators:', err);
      return [];
    }
  }

  // ─── Transaction Builders ──────────────────────────────────────────────────────

  /** Build stake transaction (SOL → mSOL). */
  async buildStake(
    amount: string,
    slippageBps?: number
  ): Promise<MarinadeBuildResponse | null> {
    return this.buildAction('marinade_stake', { amount, slippageBps });
  }

  /** Build unstake transaction (mSOL → SOL, instant). */
  async buildUnstake(
    amount: string,
    slippageBps?: number
  ): Promise<MarinadeBuildResponse | null> {
    return this.buildAction('marinade_unstake', { amount, slippageBps });
  }

  /** Build delayed unstake transaction (mSOL → SOL, ~2 epochs). */
  async buildDelayedUnstake(
    amount: string
  ): Promise<MarinadeBuildResponse | null> {
    return this.buildAction('marinade_delayed_unstake', { amount });
  }

  // ─── Private Helpers ────────────────────────────────────────────────────────

  private async buildAction(
    type: string,
    params: Record<string, unknown>
  ): Promise<MarinadeBuildResponse | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<MarinadeBuildResponse>(
          `${environment.apiBase}/solana/actions/build`,
          { type, params }
        )
      );
      return resp;
    } catch (err) {
      console.error(`Marinade build ${type} error:`, err);
      return null;
    }
  }
}

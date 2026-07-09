import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '@core/services/api.service';

/**
 * Cost-basis row for a single mint. Persisted server-side in
 * `chat_schema.wallet_token_costbasis`; emitted by the gateway-proxied
 * `GET /portfolio/pnl/{wallet}` endpoint after walking Helius enhanced
 * transactions and pricing each leg at the slot via Birdeye history.
 *
 * The frontend joins this against current balances + current prices to
 * compute realized + unrealized PnL — the math lives client-side so it
 * stays in sync with the live ticker without invalidating a server cache.
 */
export interface TokenCostBasis {
  mint: string;
  totalBoughtAmount: number;
  totalBoughtUsd: number;
  totalSoldAmount: number;
  totalSoldUsd: number;
  lastProcessedAt: string | null;
}

interface PnlResponse {
  wallet: string;
  positions: TokenCostBasis[];
}

interface RefreshResponse {
  status: 'ok' | 'debounced';
  summary?: { status: string; pages: number; txs: number; mints: number; incremental: boolean };
  retry_after_seconds?: number;
}

@Injectable({ providedIn: 'root' })
export class PortfolioAnalyticsService {
  private readonly api = inject(ApiService);

  /**
   * Read the persisted cost-basis snapshot for `wallet`. Returns an empty
   * array when the server has never synced this wallet — the page will
   * render "—" in the PnL column for that case (rather than ship a fake
   * zero) and the caller is expected to fire `refreshCostBasis` to kick
   * off the first backfill.
   */
  async getCostBasis(wallet: string): Promise<TokenCostBasis[]> {
    try {
      const resp = await firstValueFrom(
        this.api.get<PnlResponse>(`/portfolio/pnl/${wallet}`),
      );
      return resp.positions ?? [];
    } catch {
      return [];
    }
  }

  /**
   * Ask the server to scan new Helius transactions and update the cost
   * basis. Debounced 5 minutes per wallet on the server, so the frontend
   * can safely fire this on every portfolio open.
   *
   * Fire-and-forget — we don't await the body because the parser walks
   * up to 30 pages of Helius txs on initial backfill and that can take
   * 10–30 seconds. The frontend re-reads `getCostBasis` after a short
   * delay on first load to pick up the freshly-written row(s).
   */
  refreshCostBasis(wallet: string): Promise<RefreshResponse | null> {
    return firstValueFrom(
      this.api.post<RefreshResponse>(`/portfolio/refresh/${wallet}`, {}),
    ).catch(() => null);
  }

  /**
   * Compute realized + unrealized + total all-time PnL for one holding
   * from its cost basis row and the *current* balance + price.
   *
   * Realized   = totalSoldUsd − avgCost × totalSoldAmount
   * Unrealized = (currentPrice − avgCost) × currentBalance
   * Total      = realized + unrealized
   *
   * Returns null when there's no usable cost basis (zero bought) — the
   * UI renders "—" in that case rather than a misleading "+$0".
   */
  computePnl(
    basis: TokenCostBasis,
    currentBalance: number,
    currentPrice: number | null,
  ): { realizedUsd: number; unrealizedUsd: number; totalUsd: number; totalPct: number } | null {
    if (basis.totalBoughtAmount <= 0) return null;
    const avgCost = basis.totalBoughtUsd / basis.totalBoughtAmount;
    if (!isFinite(avgCost) || avgCost <= 0) return null;

    const realizedUsd = basis.totalSoldUsd - avgCost * basis.totalSoldAmount;
    const unrealizedUsd =
      currentPrice != null && currentBalance > 0
        ? (currentPrice - avgCost) * currentBalance
        : 0;
    const totalUsd = realizedUsd + unrealizedUsd;
    // Percent is taken against total bought USD — a clean baseline that
    // mirrors how brokerages report "return on capital deployed".
    const totalPct = (totalUsd / basis.totalBoughtUsd) * 100;
    return { realizedUsd, unrealizedUsd, totalUsd, totalPct };
  }
}

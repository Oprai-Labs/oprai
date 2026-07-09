/**
 * YieldScannerService
 *
 * Aggregates cross-protocol APY data and surfaces yield opportunities.
 * All numbers come from live protocol APIs — no static fallback table.
 * When a source is unreachable, the corresponding entry is omitted rather
 * than backfilled with a stale constant (which previously surfaced
 * months-old APYs as if they were current).
 */

import { Injectable, inject } from '@angular/core';
import { KaminoService, KAMINO_MAIN_MARKET } from './market/kamino.service';
import { JitoService } from './market/jito.service';
import { MarinadeService } from './market/marinade.service';

// ── Types ────────────────────────────────────────────────────────────────────

export interface ProtocolYield {
  protocol: string;
  protocolLabel: string;
  token: string;             // e.g. "USDC", "SOL"
  mint: string;
  type: 'lend' | 'stake' | 'lp';
  supplyApy: number;         // annualised % (e.g. 8.5 = 8.5%)
  borrowApy?: number;
  tvlUsd?: number;
  /** Action type to deposit/move to this protocol */
  actionType: string;
  actionParams: Record<string, string>;
  /** Risk score 1-5 (1=lowest risk) */
  riskScore: number;
}

export interface YieldOpportunity {
  token: string;
  mint: string;
  currentProtocol: string;
  currentApy: number;
  bestProtocol: ProtocolYield;
  apyGain: number;
  estimatedAnnualGainUsd?: number;
  actionType: string;
  actionParams: Record<string, string>;
}

// ── Constants used to wire action params ─────────────────────────────────────

const SOL_MINT = 'So11111111111111111111111111111111111111112';

// ── Service ──────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class YieldScannerService {
  private readonly kamino = inject(KaminoService);
  private readonly jito = inject(JitoService);
  private readonly marinade = inject(MarinadeService);

  /** Cache the full yield list briefly so multiple tabs/components don't refetch. */
  private cache: { yields: ProtocolYield[]; ts: number } | null = null;
  private readonly CACHE_MS = 30_000;

  /**
   * Fetch every yield we have a live source for. Currently:
   *   - Kamino: every reserve across every Kamino lending market
   *   - Jito:   jitoSOL liquid stake APY (SOL → jitoSOL)
   *   - Marinade: mSOL liquid stake APY (SOL → mSOL)
   * Anything else (MarginFi, Solend, JupSOL, …) is omitted until a live
   * APY source is wired — we never inject made-up numbers.
   */
  private async fetchAllYields(): Promise<ProtocolYield[]> {
    const out: ProtocolYield[] = [];

    // ── Kamino: enumerate markets, then reserves per market ─────────
    try {
      const markets = await this.kamino.getMarkets();
      // Run reserve lookups in parallel; one slow market shouldn't gate the rest.
      const perMarket = await Promise.all(markets.map(async (m) => {
        try { return { market: m, reserves: await this.kamino.getMarketReserves(m.lendingMarket) }; }
        catch { return { market: m, reserves: [] }; }
      }));
      for (const { market, reserves } of perMarket) {
        for (const r of reserves) {
          const apy = (parseFloat(r.supplyApy ?? '0') || 0) * 100;
          if (!(apy > 0)) continue;
          out.push({
            protocol: 'kamino',
            protocolLabel: `Kamino Lend${market.isPrimary ? '' : ` — ${market.name}`}`,
            token: r.liquidityToken,
            mint: r.liquidityTokenMint,
            type: 'lend',
            supplyApy: apy,
            borrowApy: (parseFloat(r.borrowApy ?? '0') || 0) * 100,
            tvlUsd: r.totalSupplyUsd,
            actionType: 'kamino_deposit',
            actionParams: {
              market: market.lendingMarket,
              mint: r.liquidityTokenMint,
            },
            riskScore: 2,
          });
        }
      }
    } catch { /* upstream Kamino unavailable — skip silently */ }

    // ── Jito jitoSOL ────────────────────────────────────────────────
    try {
      const apy = await this.jito.getCurrentApy();
      if (typeof apy === 'number' && apy > 0) {
        out.push({
          protocol: 'jito',
          protocolLabel: 'Jito (jitoSOL)',
          token: 'SOL',
          mint: SOL_MINT,
          type: 'stake',
          supplyApy: apy,
          actionType: 'jito_stake',
          actionParams: {},
          riskScore: 1,
        });
      }
    } catch { /* skip */ }

    // ── Marinade mSOL ───────────────────────────────────────────────
    try {
      const stats = await this.marinade.getStats();
      const apy = stats?.apy;
      if (typeof apy === 'number' && apy > 0) {
        out.push({
          protocol: 'marinade',
          protocolLabel: 'Marinade (mSOL)',
          token: 'SOL',
          mint: SOL_MINT,
          type: 'stake',
          supplyApy: apy,
          tvlUsd: stats?.tvl ?? undefined,
          actionType: 'marinade_stake',
          actionParams: {},
          riskScore: 1,
        });
      }
    } catch { /* skip */ }

    return out;
  }

  private async getAllCached(): Promise<ProtocolYield[]> {
    if (this.cache && Date.now() - this.cache.ts < this.CACHE_MS) {
      return this.cache.yields;
    }
    const yields = await this.fetchAllYields();
    this.cache = { yields, ts: Date.now() };
    return yields;
  }

  /**
   * Get yields for a specific token symbol, highest APY first. Returns
   * an empty list when no live source is reachable — callers should
   * render an "unavailable" state rather than show stale numbers.
   */
  async getYieldsForToken(token: string): Promise<ProtocolYield[]> {
    const sym = token.toUpperCase();
    const all = await this.getAllCached();
    return all
      .filter((y) => y.token.toUpperCase() === sym)
      .sort((a, b) => b.supplyApy - a.supplyApy);
  }

  /**
   * Match holdings against live yields and surface opportunities sorted
   * by estimated annual USD gain. Skips holdings below $10 (dust).
   */
  async findOpportunities(
    holdings: Array<{ token: string; mint: string; amountUsd: number; currentProtocol?: string; currentApy?: number }>,
    options: { minGainPct?: number; maxRisk?: number } = {}
  ): Promise<YieldOpportunity[]> {
    const { minGainPct = 0.5, maxRisk = 4 } = options;
    const opps: YieldOpportunity[] = [];

    await Promise.all(holdings.map(async (holding) => {
      if (holding.amountUsd < 10) return;
      const yields = await this.getYieldsForToken(holding.token);
      const eligible = yields.filter((y) => y.riskScore <= maxRisk);
      if (eligible.length === 0) return;

      const best = eligible[0];
      const currentApy = holding.currentApy ?? 0;
      const apyGain = best.supplyApy - currentApy;
      if (apyGain < minGainPct) return;

      const estimatedAnnualGainUsd = (holding.amountUsd * apyGain) / 100;
      const params = { ...best.actionParams, amount: holding.amountUsd.toFixed(2) };

      opps.push({
        token: holding.token,
        mint: holding.mint,
        currentProtocol: holding.currentProtocol ?? 'wallet (idle)',
        currentApy,
        bestProtocol: best,
        apyGain,
        estimatedAnnualGainUsd,
        actionType: best.actionType,
        actionParams: params,
      });
    }));

    return opps.sort((a, b) => (b.estimatedAnnualGainUsd ?? 0) - (a.estimatedAnnualGainUsd ?? 0));
  }

  /** All yields we have a live signal for, sorted by APY. */
  async getAllYields(): Promise<ProtocolYield[]> {
    const all = await this.getAllCached();
    return [...all].sort((a, b) => b.supplyApy - a.supplyApy);
  }
}

// Re-export so callers don't import from kamino.service directly.
export { KAMINO_MAIN_MARKET };

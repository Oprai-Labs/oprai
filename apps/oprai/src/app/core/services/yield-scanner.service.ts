/**
 * YieldScannerService
 *
 * Aggregates cross-protocol APY data and calculates yield
 * opportunities based on user portfolio.
 *
 * Desteklenen protokoller:
 *   - Kamino K-Lend (supply APY per reserve)
 *   - MarginFi (deposit APY per bank)
 *   - Solend (supply APY per reserve)
 *   - Jito (jitoSOL stake APY)
 *   - Marinade (mSOL stake APY)
 *   - JupSOL (stake APY)
 */

import { Injectable, inject } from '@angular/core';
import { KaminoService } from './market/kamino.service';
import { JitoService } from './market/jito.service';

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
  /** Where idle/currently-earning tokens are now */
  currentProtocol: string;
  currentApy: number;
  /** Best alternative */
  bestProtocol: ProtocolYield;
  /** APY improvement */
  apyGain: number;
  /** Estimated annual gain in USD (requires amount) */
  estimatedAnnualGainUsd?: number;
  /** Action to move tokens */
  actionType: string;
  actionParams: Record<string, string>;
}

// ── Static fallback APY data (updated periodically from docs) ─────────────────
// These represent typical/base APYs — real data is fetched when available

const STATIC_APY: Record<string, ProtocolYield[]> = {
  SOL: [
    {
      protocol: 'jito',
      protocolLabel: 'Jito (jitoSOL)',
      token: 'SOL',
      mint: 'So11111111111111111111111111111111111111112',
      type: 'stake',
      supplyApy: 7.8,
      tvlUsd: 2_500_000_000,
      actionType: 'jito_stake',
      actionParams: {},
      riskScore: 1,
    },
    {
      protocol: 'marinade',
      protocolLabel: 'Marinade (mSOL)',
      token: 'SOL',
      mint: 'So11111111111111111111111111111111111111112',
      type: 'stake',
      supplyApy: 7.2,
      tvlUsd: 1_500_000_000,
      actionType: 'marinade_stake',
      actionParams: {},
      riskScore: 1,
    },
    {
      protocol: 'jupsol',
      protocolLabel: 'Jupiter (jupSOL)',
      token: 'SOL',
      mint: 'So11111111111111111111111111111111111111112',
      type: 'stake',
      supplyApy: 7.5,
      tvlUsd: 800_000_000,
      actionType: 'jupsol_stake',
      actionParams: {},
      riskScore: 1,
    },
    {
      protocol: 'kamino',
      protocolLabel: 'Kamino Lend',
      token: 'SOL',
      mint: 'So11111111111111111111111111111111111111112',
      type: 'lend',
      supplyApy: 3.2,
      tvlUsd: 600_000_000,
      actionType: 'kamino_deposit',
      actionParams: { market: '7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF', mint: 'So11111111111111111111111111111111111111112' },
      riskScore: 2,
    },
    {
      protocol: 'marginfi',
      protocolLabel: 'MarginFi',
      token: 'SOL',
      mint: 'So11111111111111111111111111111111111111112',
      type: 'lend',
      supplyApy: 3.8,
      tvlUsd: 400_000_000,
      actionType: 'marginfi_deposit',
      actionParams: { bank: 'So11111111111111111111111111111111111111112', amount: '' },
      riskScore: 2,
    },
  ],
  USDC: [
    {
      protocol: 'kamino',
      protocolLabel: 'Kamino Lend',
      token: 'USDC',
      mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
      type: 'lend',
      supplyApy: 8.5,
      tvlUsd: 800_000_000,
      actionType: 'kamino_deposit',
      actionParams: { market: '7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF', mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v' },
      riskScore: 2,
    },
    {
      protocol: 'marginfi',
      protocolLabel: 'MarginFi',
      token: 'USDC',
      mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
      type: 'lend',
      supplyApy: 7.2,
      tvlUsd: 600_000_000,
      actionType: 'marginfi_deposit',
      actionParams: { bank: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', amount: '' },
      riskScore: 2,
    },
    {
      protocol: 'solend',
      protocolLabel: 'Solend',
      token: 'USDC',
      mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
      type: 'lend',
      supplyApy: 5.8,
      tvlUsd: 300_000_000,
      actionType: 'kamino_deposit',
      actionParams: { pool: 'main', mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v' },
      riskScore: 2,
    },

  ],
  USDT: [
    {
      protocol: 'kamino',
      protocolLabel: 'Kamino Lend',
      token: 'USDT',
      mint: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
      type: 'lend',
      supplyApy: 8.1,
      tvlUsd: 200_000_000,
      actionType: 'kamino_deposit',
      actionParams: { market: '7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF', mint: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB' },
      riskScore: 2,
    },
    {
      protocol: 'marginfi',
      protocolLabel: 'MarginFi',
      token: 'USDT',
      mint: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
      type: 'lend',
      supplyApy: 6.9,
      tvlUsd: 100_000_000,
      actionType: 'marginfi_deposit',
      actionParams: { bank: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', amount: '' },
      riskScore: 2,
    },
  ],
};

// ── Service ──────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class YieldScannerService {
  private readonly kamino = inject(KaminoService);
  private readonly jito = inject(JitoService);

  /**
   * Get all available yields for a token symbol, sorted by APY descending.
   * Merges static fallback data with live data when available.
   */
  async getYieldsForToken(token: string): Promise<ProtocolYield[]> {
    const yields = [...(STATIC_APY[token.toUpperCase()] ?? [])];

    // Try to enrich with live Kamino data
    try {
      const markets = await this.kamino.getMarkets();
      for (const market of markets) {
        const reserves = await this.kamino.getMarketReserves(market.lendingMarket);
        for (const reserve of reserves) {
          const sym = reserve.symbol?.toUpperCase();
          if (sym !== token.toUpperCase()) continue;
          const supplyApy = parseFloat(reserve.supplyApy ?? '0') * 100;
          if (!isFinite(supplyApy) || supplyApy <= 0) continue;
          const existing = yields.find(y => y.protocol === 'kamino' && y.token.toUpperCase() === sym);
          if (existing) {
            existing.supplyApy = supplyApy;
          }
        }
      }
    } catch { /* use static */ }

    // Try to enrich with live Jito APY
    if (token.toUpperCase() === 'SOL') {
      try {
        const apy = await this.jito.getCurrentApy();
        if (apy !== null) {
          const jitoEntry = yields.find(y => y.protocol === 'jito');
          if (jitoEntry) jitoEntry.supplyApy = apy;
        }
      } catch { /* use static */ }
    }

    return yields.sort((a, b) => b.supplyApy - a.supplyApy);
  }

  /**
   * Given a list of token holdings (token → amount in USD),
   * return yield opportunities sorted by estimated annual gain.
   */
  async findOpportunities(
    holdings: Array<{ token: string; mint: string; amountUsd: number; currentProtocol?: string; currentApy?: number }>,
    options: { minGainPct?: number; maxRisk?: number } = {}
  ): Promise<YieldOpportunity[]> {
    const { minGainPct = 0.5, maxRisk = 4 } = options;
    const opps: YieldOpportunity[] = [];

    await Promise.all(holdings.map(async holding => {
      if (holding.amountUsd < 10) return; // Skip dust
      const yields = await this.getYieldsForToken(holding.token);
      const eligible = yields.filter(y => y.riskScore <= maxRisk);
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

  /**
   * Get all yields across all tracked tokens, sorted by APY.
   */
  async getAllYields(): Promise<ProtocolYield[]> {
    const tokens = Object.keys(STATIC_APY);
    const results = await Promise.all(tokens.map(t => this.getYieldsForToken(t)));
    return results.flat().sort((a, b) => b.supplyApy - a.supplyApy);
  }
}

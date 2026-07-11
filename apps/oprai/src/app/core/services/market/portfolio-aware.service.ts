/**
 * Portfolio-Aware Action Service
 *
 * Provides intelligent action suggestions based on user's portfolio.
 * Analyzes holdings and suggests:
 * - Yield optimization opportunities
 * - Portfolio rebalancing
 * - Risk management
 * - Gas optimization
 */
import { Injectable, inject } from '@angular/core';
import { Connection, PublicKey } from '@solana/web3.js';
import { createSolanaConnection } from '@core/utils/solana-connection';
import { firstValueFrom } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../../environments/environment';
import { TokenRegistryService, TokenMeta } from './token-registry.service';

export interface PortfolioPosition {
  mint: string;
  symbol: string;
  amount: number;
  usdValue: number;
  category: TokenCategory;
}

export type TokenCategory =
  | 'stable'
  | 'volatile'
  | 'liquid-staking'
  | 'perp'
  | 'nft'
  | 'unknown';

export interface ActionSuggestion {
  type: string;
  priority: 'high' | 'medium' | 'low';
  reason: string;
  potentialYield?: number;
  riskLevel: 'low' | 'medium' | 'high';
  params?: Record<string, string>;
}

export interface PortfolioAnalysis {
  totalValueUsd: number;
  positions: PortfolioPosition[];
  allocation: {
    stable: number;
    volatile: number;
    staking: number;
    other: number;
  };
  suggestions: ActionSuggestion[];
}

const STABLE_COINS = new Set(['USDC', 'USDT', 'DAI', 'FRAX', 'USDD']);
const LST_TOKENS = new Set([
  'JupSOL', 'JITO', 'MSO', 'bSOL', 'cSLD',
  'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v',
  // Canonical jitoSOL mint ends in `kGCPn`, not `kongC` (typo from copy-paste).
  // The wrong suffix would silently miss any wallet actually holding jitoSOL.
  'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn',
  'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So',
  'bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1',
]);

const YIELD_OPPORTUNITIES = [
  { mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', symbol: 'USDC', protocol: 'jupiter_lend', yield: 4.5, action: 'lend', type: 'earn' },
  { mint: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', symbol: 'USDT', protocol: 'jupiter_lend', yield: 4.2, action: 'lend', type: 'earn' },
  { mint: 'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v', symbol: 'JupSOL', protocol: 'jupsol', yield: 7.5, action: 'jupsol_stake', type: 'stake' },
  { mint: 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn', symbol: 'JitoSOL', protocol: 'jito', yield: 6.8, action: 'jito_stake', type: 'stake' },
];

@Injectable({ providedIn: 'root' })
export class PortfolioAwareService {
  private readonly http = inject(HttpClient);
  private readonly tokenRegistry = inject(TokenRegistryService);
  private connection: Connection;

  constructor() {
    this.connection = createSolanaConnection('confirmed');
  }

  /**
   * Analyze wallet portfolio and generate action suggestions.
   */
  async analyzePortfolio(walletAddress: string): Promise<PortfolioAnalysis> {
    const positions = await this.getTokenPositions(walletAddress);
    const totalValueUsd = positions.reduce((sum, p) => sum + p.usdValue, 0);

    const allocation = this.calculateAllocation(positions, totalValueUsd);
    const suggestions = await this.generateSuggestions(positions, allocation, totalValueUsd);

    return {
      totalValueUsd,
      positions,
      allocation,
      suggestions,
    };
  }

  /**
   * Get all token positions for a wallet.
   */
  private async getTokenPositions(walletAddress: string): Promise<PortfolioPosition[]> {
    const wallet = new PublicKey(walletAddress);

    // Get both SPL and Token2022 accounts
    const [splAccounts, token2022Accounts] = await Promise.all([
      this.connection.getParsedTokenAccountsByOwner(wallet, {
        programId: new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss613VQ6DA'),
      }),
      this.connection.getParsedTokenAccountsByOwner(wallet, {
        programId: new PublicKey('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'),
      }),
    ]);

    const positions: PortfolioPosition[] = [];
    const seenMints = new Set<string>();

    for (const account of [...splAccounts.value, ...token2022Accounts.value]) {
      const info = (account.account.data as any).parsed?.info;
      if (!info?.mint || !info.tokenAmount?.uiAmount) continue;

      const mint = info.mint;
      if (seenMints.has(mint)) continue;
      seenMints.add(mint);

      const amount = info.tokenAmount.uiAmount;
      if (amount <= 0) continue;

      // Get token metadata and price
      const meta = await this.getTokenMetadata(mint);
      const price = await this.getTokenPrice(mint);
      const usdValue = amount * price;
      const category = this.categorizeToken(mint, meta?.symbol ?? '');

      positions.push({
        mint,
        symbol: meta?.symbol ?? this.shortenMint(mint),
        amount,
        usdValue,
        category,
      });
    }

    // Add SOL balance
    try {
      const solBalance = await this.connection.getBalance(wallet);
      const solPrice = await this.getTokenPrice('So11111111111111111111111111111111111111112');
      const solAmount = solBalance / 1e9;
      positions.push({
        mint: 'So11111111111111111111111111111111111111112',
        symbol: 'SOL',
        amount: solAmount,
        usdValue: solAmount * solPrice,
        category: 'volatile',
      });
    } catch {}

    // Sort by USD value
    return positions.sort((a, b) => b.usdValue - a.usdValue);
  }

  /**
   * Calculate portfolio allocation percentages.
   */
  private calculateAllocation(positions: PortfolioPosition[], totalValueUsd: number): PortfolioAnalysis['allocation'] {
    if (totalValueUsd === 0) {
      return { stable: 0, volatile: 0, staking: 0, other: 0 };
    }

    let stable = 0, volatile = 0, staking = 0;

    for (const pos of positions) {
      const pct = pos.usdValue / totalValueUsd;
      switch (pos.category) {
        case 'stable': stable += pct; break;
        case 'volatile': volatile += pct; break;
        case 'liquid-staking': staking += pct; break;
        default: break;
      }
    }

    return {
      stable: Math.round(stable * 100),
      volatile: Math.round(volatile * 100),
      staking: Math.round(staking * 100),
      other: Math.round((1 - stable - volatile - staking) * 100),
    };
  }

  /**
   * Generate action suggestions based on portfolio.
   */
  private async generateSuggestions(
    positions: PortfolioPosition[],
    allocation: PortfolioAnalysis['allocation'],
    totalValueUsd: number
  ): Promise<ActionSuggestion[]> {
    const suggestions: ActionSuggestion[] = [];

    // 1. High stablecoin allocation → suggest yield
    if (allocation.stable > 50 && totalValueUsd > 100) {
      const stablePositions = positions.filter(p => p.category === 'stable' && p.usdValue > 50);
      for (const pos of stablePositions) {
        const opportunity = YIELD_OPPORTUNITIES.find(o => o.mint === pos.mint);
        if (opportunity) {
          suggestions.push({
            type: opportunity.action,
            priority: 'high',
            reason: `Earn ${opportunity.yield}% APY on your ${pos.symbol} holdings`,
            potentialYield: opportunity.yield,
            riskLevel: 'low',
            params: { amount: 'all', token: pos.symbol },
          });
        }
      }
    }

    // 2. High SOL allocation → suggest staking
    const solPosition = positions.find(p => p.symbol === 'SOL');
    if (solPosition && solPosition.usdValue > 100 && allocation.staking < 20) {
      suggestions.push({
        type: 'jupsol_stake',
        priority: 'medium',
        reason: `Earn ~7.5% APY by staking your SOL instead of holding`,
        potentialYield: 7.5,
        riskLevel: 'low',
        params: { amount: 'all' },
      });
    }

    // 3. No staking positions → suggest diversification
    if (allocation.staking < 10 && totalValueUsd > 500) {
      suggestions.push({
        type: 'jito_stake',
        priority: 'medium',
        reason: 'Diversify into liquid staking tokens for better yield',
        potentialYield: 6.8,
        riskLevel: 'low',
        params: { amount: '100' },
      });
    }

    // 4. Very low volatile allocation (too conservative)
    if (allocation.volatile < 10 && totalValueUsd > 1000) {
      suggestions.push({
        type: 'swap',
        priority: 'low',
        reason: 'Consider small DeFi exposure for growth potential',
        riskLevel: 'medium',
        params: { inputMint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', outputMint: 'SOL', amount: '50' },
      });
    }

    // 5. Large stable position → suggest lending
    const largestStable = positions.find(p => p.category === 'stable' && p.usdValue > 1000);
    if (largestStable) {
      suggestions.push({
        type: 'lend',
        priority: 'high',
        reason: `Put your $${largestStable.usdValue.toFixed(0)} ${largestStable.symbol} to work with ~4% APY`,
        potentialYield: 4.5,
        riskLevel: 'low',
        params: { amount: 'all', token: largestStable.symbol },
      });
    }

    return suggestions;
  }

  /**
   * Categorize token type.
   */
  private categorizeToken(mint: string, symbol: string): TokenCategory {
    const upper = symbol.toUpperCase();

    if (STABLE_COINS.has(upper)) return 'stable';
    if (LST_TOKENS.has(mint) || LST_TOKENS.has(upper)) return 'liquid-staking';
    if (upper.includes('NFT')) return 'nft';
    if (upper.includes('JLP') || upper.includes('perp')) return 'perp';

    return 'volatile';
  }

  /**
   * Get token metadata from registry.
   */
  private async getTokenMetadata(_mint: string): Promise<TokenMeta | null> {
    try {
      await this.tokenRegistry.ensureLoaded();
      // Try to get by symbol first, then just return null for unknown
      return null;
    } catch {
      return null;
    }
  }

  /**
   * Get token price from backend.
   */
  private async getTokenPrice(mint: string): Promise<number> {
    try {
      const res = await firstValueFrom(
        this.http.get<any>(`${environment.apiBase}/market/price/${mint}`)
      );
      return res.price ?? 0;
    } catch {
      // Fallback prices for common tokens
      const FALLBACK_PRICES: Record<string, number> = {
        'So11111111111111111111111111111111111111112': 180, // SOL
        'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v': 1, // USDC
        'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': 1, // USDT
      };
      return FALLBACK_PRICES[mint] ?? 0;
    }
  }

  /**
   * Shorten mint address for display.
   */
  private shortenMint(mint: string): string {
    return `${mint.slice(0, 4)}...${mint.slice(-4)}`;
  }

  /**
   * Quick check if wallet has any yield opportunities.
   */
  async hasYieldOpportunities(walletAddress: string): Promise<boolean> {
    const positions = await this.getTokenPositions(walletAddress);
    return positions.some(p =>
      p.category === 'stable' && p.usdValue > 50
    );
  }

  /**
   * Get top yield opportunities for wallet.
   */
  async getTopYieldOpportunities(walletAddress: string, limit = 3): Promise<ActionSuggestion[]> {
    const analysis = await this.analyzePortfolio(walletAddress);
    return analysis.suggestions
      .filter(s => s.potentialYield && s.potentialYield > 3)
      .sort((a, b) => (b.potentialYield ?? 0) - (a.potentialYield ?? 0))
      .slice(0, limit);
  }
}

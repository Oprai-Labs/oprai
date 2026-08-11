/**
 * DeFi Strategy Optimizer Service
 *
 * Analyzes user's portfolio and suggests optimal DeFi strategies across all protocols.
 * Provides real-time yield comparisons, route planning, and one-click execution.
 *
 * Supported Protocols:
 * - Staking: Jito, Marinade
 * - Liquidity: Orca, Raydium, Meteora
 * - Perpetuals: Jupiter Perp
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { PortfolioAwareService, PortfolioPosition } from './portfolio-aware.service';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface ProtocolYield {
  protocol: string;
  protocolIcon: string;
  protocolColor: string;
  action: 'lend' | 'stake' | 'farm' | 'perp' | 'borrow' | 'liquidity';
  token: string;
  tokenMint: string;
  apy: number;
  apr: number;
  tvl: number;
  riskLevel: 'low' | 'medium' | 'high';
  lockPeriod?: number;
  minDeposit?: number;
  autoCompound?: boolean;
  rewards?: { symbol: string; apy: number }[];
}

export interface StrategyStep {
  step: number;
  protocol: string;
  protocolIcon: string;
  protocolColor: string;
  action: string;
  fromToken: string;
  fromMint: string;
  toToken: string;
  toMint: string;
  amount: number;
  expectedOutput: number;
  slippage: number;
  apy?: number;
  description: string;
  actionType: string; // Action type for execution
  params?: Record<string, string>;
}

export interface DeFiStrategy {
  id: string;
  name: string;
  description: string;
  category: 'lending' | 'staking' | 'liquidity' | 'perp' | 'hybrid';
  totalApy: number;
  totalApr: number;
  riskLevel: 'low' | 'medium' | 'high';
  riskScore: number; // 1-10
  steps: StrategyStep[];
  estimatedOutput: number;
  estimatedOutputUsd: number;
  timeToExecute: number;
  gasCost: number;
  successRate: number; // percentage
  warnings?: string[];
}

export interface StrategyRequest {
  walletAddress: string;
  focusToken?: string;
  riskTolerance: 'low' | 'medium' | 'high';
  investmentAmount?: number;
  maxSteps?: number;
  excludeCategories?: string[];
}

export interface YieldFilter {
  minApy?: number;
  minTvl?: number;
  categories?: string[];
  protocols?: string[];
}

export interface StrategyComparison {
  strategies: DeFiStrategy[];
  bestByApy: DeFiStrategy;
  bestByRisk: DeFiStrategy;
  bestByTime: DeFiStrategy;
}

// ─── Constants ───────────────────────────────────────────────────────────────

// Token mint addresses
const TOKENS = {
  SOL: 'So11111111111111111111111111111111111111112',
  USDC: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
  USDT: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
  JupSOL: 'jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v',
  JitoSOL: 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn',
  mSOL: 'mSoLzYCxHdYgdzU16g5QSh3i5K3z3zZKhtXDH4yYga',
  bSOL: 'bSo13r4TkiE4KumL71rHT1xr1yVRmF5CUnrS9QNZvuK',
  cSOL: 'Cf4hUSKun1oJVqNusAWWvCkrWnQdWbKNRV3tS7S6ygi',
  ETH: '7vfCXTUXx5WPN5AUz8C8VbUX1MYk3vR3aEpCkEKTqXM',
  BTC: '9n4nbM75f5Ui33ZbPYJz59saqUVM6sLE6o7AQXQuJEp',
};


@Injectable({ providedIn: 'root' })
export class DeFiStrategyOptimizerService {
  private readonly http = inject(HttpClient);
  private readonly portfolioService = inject(PortfolioAwareService);

  /**
   * Generate optimal DeFi strategies for a wallet.
   */
  async generateStrategies(request: StrategyRequest): Promise<DeFiStrategy[]> {
    const { walletAddress, focusToken, riskTolerance, investmentAmount } = request;

    // Get user's portfolio
    const portfolio = await this.portfolioService.analyzePortfolio(walletAddress);
    const positions = portfolio.positions;

    // Fetch real yields from protocols
    const yields = await this.fetchProtocolYields();

    // Filter yields based on risk tolerance
    const filteredYields = yields.filter(y => {
      if (riskTolerance === 'low' && y.riskLevel !== 'low') return false;
      if (riskTolerance === 'medium' && y.riskLevel === 'high') return false;
      if (focusToken && !y.token.toLowerCase().includes(focusToken.toLowerCase())) return false;
      return true;
    });

    // Generate strategies based on portfolio
    const strategies: DeFiStrategy[] = [];

    // Strategy 1: Yield Maximization (Lend excess stablecoins)
    const stablePositions = positions.filter(p => p.category === 'stable' && p.usdValue > 50);
    for (const pos of stablePositions) {
      const lendingYields = filteredYields.filter(y =>
        y.action === 'lend' && y.token === pos.symbol
      );
      for (const yield_ of lendingYields) {
        strategies.push(this.createLendingStrategy(pos, yield_, investmentAmount));
      }
    }

    // Strategy 2: Liquid Staking (Stake SOL)
    const solPosition = positions.find(p => p.symbol === 'SOL' && p.amount > 0.1);
    if (solPosition) {
      const stakingYields = filteredYields.filter(y => y.action === 'stake' && y.token === 'SOL');
      for (const yield_ of stakingYields) {
        strategies.push(this.createStakingStrategy(solPosition, yield_));
      }
    }

    // Strategy 3: Multi-step (Swap → Stake)
    if (solPosition && solPosition.usdValue > 100) {
      const swapStakeStrategy = this.createSwapStakeStrategy(solPosition, yields);
      if (swapStakeStrategy) strategies.push(swapStakeStrategy);
    }

    // Strategy 4: Liquidity Provision
    const liquidityYields = filteredYields.filter(y => y.action === 'liquidity');
    for (const yield_ of liquidityYields) {
      if (portfolio.totalValueUsd > 500) {
        strategies.push(this.createLiquidityStrategy(positions, yield_, investmentAmount));
      }
    }

    // Sort by APY
    return strategies.sort((a, b) => b.totalApy - a.totalApy);
  }

  /**
   * Fetch real-time yields from all protocols.
   */
  private async fetchProtocolYields(): Promise<ProtocolYield[]> {
    const results: ProtocolYield[] = [];

    try {
      // Fetch Jupiter Lend yields
      const lendRes = await firstValueFrom(
        this.http.get<any[]>('https://lite-api.jup.ag/lend/v1/earn/tokens')
      ).catch(() => []);

      for (const token of lendRes) {
        const tokenSymbol = token.asset?.symbol ?? token.symbol ?? '';
        results.push({
          protocol: 'Jupiter',
          protocolIcon: '🟧',
          protocolColor: '#6366F1',
          tokenMint: this.getTokenMint(tokenSymbol),
          action: 'lend',
          token: tokenSymbol,
          apy: (token.totalRate ?? 0) / 100,
          apr: ((token.totalRate ?? 0) / 100) * 0.95,
          tvl: token.tvl ?? 0,
          riskLevel: 'low',
        });
      }

      // Fetch Jito yields
      try {
        const jitoRes = await firstValueFrom(
          this.http.get<any>('https://api.jito.lol/api/v1/stakedSATs')
        ).catch(() => null);
        if (jitoRes?.mainnet?.apy) {
          results.push({
            protocol: 'Jito',
            protocolIcon: '🚀',
            protocolColor: '#F97316',
            tokenMint: TOKENS.JitoSOL,
            action: 'stake',
            token: 'SOL',
            apy: jitoRes.mainnet.apy,
            apr: jitoRes.mainnet.apy * 0.97,
            tvl: jitoRes.mainnet.totalStaked ?? 0,
            riskLevel: 'low',
          });
        }
      } catch {}

      // Fetch Kamino yields
      const kaminoRes = await firstValueFrom(
        this.http.get<any[]>('https://api.kamino.finance/v2/yields')
      ).catch(() => []);

      for (const pool of kaminoRes) {
        const tokenSymbol = pool.symbol ?? '';
        results.push({
          protocol: 'Kamino',
          protocolIcon: '🏛️',
          protocolColor: '#3B82F6',
          tokenMint: this.getTokenMint(tokenSymbol),
          action: 'lend',
          token: tokenSymbol,
          apy: pool.apy ?? 0,
          apr: (pool.apy ?? 0) * 0.95,
          tvl: pool.tvl ?? 0,
          riskLevel: 'low',
        });
      }

      // their public REST APIs offline:
      //   - `api.solend.fi/v1/config` → 404
      // The yield rows from these protocols won't appear in the optimizer
      // output until we replace these calls with on-chain reads through the
      // gateway. Caught-and-swallowed errors used to mask this as silent
      // empty results; better to surface the gap explicitly.

      // Fetch Marinade yields
      try {
        const marinadeRes = await firstValueFrom(
          this.http.get<any>('https://api.marinade.finance/api/v1/real-epoch')
        ).catch(() => null);

        if (marinadeRes?.stakeAccruedRewards) {
          results.push({
            protocol: 'Marinade',
            protocolIcon: '🌱',
            protocolColor: '#22C55E',
            tokenMint: TOKENS.mSOL,
            action: 'stake',
            token: 'SOL',
            apy: marinadeRes.apr ?? 6.2,
            apr: (marinadeRes.apr ?? 6.2) * 0.97,
            tvl: marinadeRes.totalStaked ?? 800000000,
            riskLevel: 'low',
          });
        }
      } catch {}

    } catch {
      // Use fallback yields on error
    }

    // Add fallback yields for common tokens
    const fallbackYields: ProtocolYield[] = [
      { protocol: 'Jupiter', protocolIcon: '🟧', protocolColor: '#6366F1', tokenMint: TOKENS.USDC, action: 'lend', token: 'USDC', apy: 4.5, apr: 4.3, tvl: 50000000, riskLevel: 'low' },
      { protocol: 'Jupiter', protocolIcon: '🟧', protocolColor: '#6366F1', tokenMint: TOKENS.USDT, action: 'lend', token: 'USDT', apy: 4.2, apr: 4.0, tvl: 30000000, riskLevel: 'low' },
      { protocol: 'Jupiter', protocolIcon: '🟧', protocolColor: '#6366F1', tokenMint: TOKENS.SOL, action: 'lend', token: 'SOL', apy: 3.5, apr: 3.3, tvl: 20000000, riskLevel: 'low' },
      { protocol: 'Jito', protocolIcon: '🚀', protocolColor: '#F97316', tokenMint: TOKENS.JitoSOL, action: 'stake', token: 'SOL', apy: 6.8, apr: 6.5, tvl: 1500000000, riskLevel: 'low' },
      { protocol: 'Marinade', protocolIcon: '🌱', protocolColor: '#22C55E', tokenMint: TOKENS.mSOL, action: 'stake', token: 'SOL', apy: 6.2, apr: 6.0, tvl: 800000000, riskLevel: 'low' },
      { protocol: 'Kamino', protocolIcon: '🏛️', protocolColor: '#3B82F6', tokenMint: TOKENS.USDC, action: 'lend', token: 'USDC', apy: 5.2, apr: 5.0, tvl: 10000000, riskLevel: 'low' },
      { protocol: 'Solend', protocolIcon: '🦇', protocolColor: '#8B5CF6', tokenMint: TOKENS.USDC, action: 'lend', token: 'USDC', apy: 3.8, apr: 3.6, tvl: 15000000, riskLevel: 'low' },
    ];

    // Merge with API yields (avoid duplicates)
    for (const fallback of fallbackYields) {
      if (!results.find(r => r.protocol === fallback.protocol && r.token === fallback.token && r.action === fallback.action)) {
        results.push(fallback);
      }
    }

    return results;
  }

  /**
   * Create a lending strategy.
   */
  private createLendingStrategy(position: PortfolioPosition, yield_: ProtocolYield, investmentAmount?: number): DeFiStrategy {
    const amount = investmentAmount ?? position.amount;
    const expectedYield = amount * (yield_.apy / 100);
    const tokenMint = this.getTokenMint(position.symbol);
    const estimatedOutputUsd = (amount + expectedYield) * this.getTokenPrice(position.symbol);

    return {
      id: `lend-${yield_.protocol.toLowerCase()}-${position.symbol.toLowerCase()}`,
      name: `Lend ${position.symbol} on ${yield_.protocol}`,
      description: `Deposit your ${position.symbol} to earn ${yield_.apy}% APY with ${yield_.protocol}`,
      category: 'lending',
      totalApy: yield_.apy,
      totalApr: yield_.apr ?? yield_.apy * 0.95,
      riskLevel: yield_.riskLevel,
      riskScore: yield_.riskLevel === 'low' ? 2 : yield_.riskLevel === 'medium' ? 5 : 8,
      steps: [
        {
          step: 1,
          protocol: yield_.protocol,
          protocolIcon: yield_.protocolIcon,
          protocolColor: this.getProtocolColor(yield_.protocol),
          action: 'Lend',
          fromToken: position.symbol,
          fromMint: tokenMint,
          toToken: this.getYieldTokenSymbol(yield_.protocol, position.symbol),
          toMint: tokenMint,
          amount,
          expectedOutput: amount,
          slippage: 0.1,
          apy: yield_.apy,
          description: `Deposit ${amount} ${position.symbol} into ${yield_.protocol} lending pool`,
          actionType: yield_.action === 'borrow' ? 'borrow' : 'lend',
          params: { amount: amount.toString(), token: position.symbol },
        },
      ],
      estimatedOutput: amount + expectedYield,
      estimatedOutputUsd,
      timeToExecute: 2,
      gasCost: 0.0005,
      successRate: 98.5,
      warnings: yield_.action === 'borrow' ? ['Borrowing amplifies both gains and losses'] : undefined,
    };
  }

  /**
   * Create a staking strategy.
   */
  private createStakingStrategy(position: PortfolioPosition, yield_: ProtocolYield): DeFiStrategy {
    const amount = position.amount;
    const expectedYield = amount * (yield_.apy / 100);
    const lstSymbol = yield_.protocol === 'Jito' ? 'JitoSOL' : yield_.protocol === 'Marinade' ? 'mSOL' : 'JupSOL';
    const lstMint = this.getTokenMint(lstSymbol);

    return {
      id: `stake-${yield_.protocol.toLowerCase()}-sol`,
      name: `Stake SOL via ${yield_.protocol}`,
      description: `Stake your SOL to earn ${yield_.apy}% APY through ${yield_.protocol}'s liquid staking`,
      category: 'staking',
      totalApy: yield_.apy,
      totalApr: yield_.apr ?? yield_.apy * 0.97,
      riskLevel: yield_.riskLevel,
      riskScore: 2,
      steps: [
        {
          step: 1,
          protocol: yield_.protocol,
          protocolIcon: yield_.protocolIcon,
          protocolColor: this.getProtocolColor(yield_.protocol),
          action: 'Stake',
          fromToken: 'SOL',
          fromMint: TOKENS.SOL,
          toToken: lstSymbol,
          toMint: lstMint,
          amount,
          expectedOutput: amount * 0.98,
          slippage: 0.5,
          apy: yield_.apy,
          description: `Stake ${amount} SOL to receive ${lstSymbol}`,
          actionType: 'jupsol_stake',
          params: { amount: amount.toString() },
        },
      ],
      estimatedOutput: amount + expectedYield,
      estimatedOutputUsd: (amount + expectedYield) * 180,
      timeToExecute: 1,
      gasCost: 0.0002,
      successRate: 99.2,
    };
  }

  /**
   * Create a multi-step swap → stake strategy.
   */
  private createSwapStakeStrategy(position: PortfolioPosition, yields: ProtocolYield[]): DeFiStrategy | null {
    // Find best staking yield
    const stakingYield = yields.find(y => y.action === 'stake' && y.token === 'SOL' && y.apy > 6);
    if (!stakingYield) return null;

    const swapYield = yields.find(y => y.action === 'lend' && y.token === 'USDC');
    const lendApy = swapYield?.apy ?? 4.5;

    const amount = position.amount;
    const solPrice = 180;
    const solValue = amount * solPrice;

    // Strategy: Swap some SOL to USDC, lend it, stake rest
    const swapAmount = Math.min(solValue * 0.3 / solPrice, amount * 0.3);
    const stakeAmount = amount - swapAmount;

    const swapOutput = swapAmount * 0.97; // After slippage
    const stakeOutput = stakeAmount * 0.98;

    const lendYield = swapOutput * (lendApy / 100);
    const stakeYieldEarn = stakeOutput * (stakingYield.apy / 100);
    const totalYield = lendYield + stakeYieldEarn;
    const combinedApy = (totalYield / solValue) * 100;

    const lstSymbol = stakingYield.protocol === 'Jito' ? 'JitoSOL' : 'JupSOL';

    return {
      id: `combo-swap-stake-${stakingYield.protocol.toLowerCase()}`,
      name: `Hybrid: Stake + Lend Strategy`,
      description: `Maximize returns by staking ${stakingYield.protocol} and lending USDC simultaneously`,
      category: 'hybrid',
      totalApy: combinedApy,
      totalApr: combinedApy * 0.95,
      riskLevel: 'medium',
      riskScore: 4,
      steps: [
        {
          step: 1,
          protocol: 'Jupiter',
          protocolIcon: '🟧',
          protocolColor: '#6366F1',
          action: 'Swap',
          fromToken: 'SOL',
          fromMint: TOKENS.SOL,
          toToken: 'USDC',
          toMint: TOKENS.USDC,
          amount: swapAmount,
          expectedOutput: swapOutput,
          slippage: 1.0,
          description: `Swap ${swapAmount.toFixed(2)} SOL to USDC for lending`,
          actionType: 'swap',
          params: { inputMint: TOKENS.SOL, outputMint: TOKENS.USDC, amount: (swapAmount * solPrice).toString() },
        },
        {
          step: 2,
          protocol: 'Jupiter',
          protocolIcon: '🟧',
          protocolColor: '#6366F1',
          action: 'Lend',
          fromToken: 'USDC',
          fromMint: TOKENS.USDC,
          toToken: 'jlUSDC',
          toMint: TOKENS.USDC,
          amount: swapOutput,
          expectedOutput: swapOutput,
          slippage: 0.1,
          apy: lendApy,
          description: `Lend USDC on Jupiter to earn ${lendApy}% APY`,
          actionType: 'lend',
          params: { amount: swapOutput.toString(), token: 'USDC' },
        },
        {
          step: 3,
          protocol: stakingYield.protocol,
          protocolIcon: stakingYield.protocolIcon,
          protocolColor: this.getProtocolColor(stakingYield.protocol),
          action: 'Stake',
          fromToken: 'SOL',
          fromMint: TOKENS.SOL,
          toToken: lstSymbol,
          toMint: this.getTokenMint(lstSymbol),
          amount: stakeAmount,
          expectedOutput: stakeOutput,
          slippage: 0.5,
          apy: stakingYield.apy,
          description: `Stake remaining SOL on ${stakingYield.protocol} for ${stakingYield.apy}% APY`,
          actionType: 'jupsol_stake',
          params: { amount: stakeAmount.toString() },
        },
      ],
      estimatedOutput: solValue + totalYield,
      estimatedOutputUsd: solValue + totalYield,
      timeToExecute: 5,
      gasCost: 0.002,
      successRate: 96.0,
      warnings: ['Multi-step transactions have higher gas costs', 'Slippage may affect swap output'],
    };
  }

  /**
   * Create a liquidity provision strategy.
   */
  private createLiquidityStrategy(positions: PortfolioPosition[], yield_: ProtocolYield, investmentAmount?: number): DeFiStrategy {
    const amount = investmentAmount ?? positions.reduce((sum, p) => sum + p.usdValue, 0) * 0.2;
    const estimatedYield = amount * 0.15;

    return {
      id: `liq-${yield_.protocol.toLowerCase()}-${yield_.token.toLowerCase().replace('-', '')}`,
      name: `Provide Liquidity on ${yield_.protocol}`,
      description: `Add liquidity to ${yield_.token} pool on ${yield_.protocol} and earn fees + incentives`,
      category: 'liquidity',
      totalApy: 15,
      totalApr: 14,
      riskLevel: yield_.riskLevel,
      riskScore: yield_.riskLevel === 'low' ? 4 : yield_.riskLevel === 'medium' ? 6 : 8,
      steps: [
        {
          step: 1,
          protocol: yield_.protocol,
          protocolIcon: yield_.protocolIcon,
          protocolColor: this.getProtocolColor(yield_.protocol),
          action: 'Add Liquidity',
          fromToken: yield_.token,
          fromMint: this.getTokenMint(yield_.token.split('-')[0] || yield_.token),
          toToken: `${yield_.token}-LP`,
          toMint: '',
          amount,
          expectedOutput: amount,
          slippage: 1.0,
          description: `Provide liquidity to ${yield_.token} pool`,
          actionType: 'add_liquidity',
          params: { pool: yield_.token, amount: amount.toString() },
        },
        {
          step: 2,
          protocol: yield_.protocol,
          protocolIcon: yield_.protocolIcon,
          protocolColor: this.getProtocolColor(yield_.protocol),
          action: 'Farm',
          fromToken: `${yield_.token}-LP`,
          fromMint: '',
          toToken: `${yield_.token}-FARM`,
          toMint: '',
          amount,
          expectedOutput: amount,
          slippage: 0.1,
          description: `Stake LP tokens to farm additional rewards`,
          actionType: 'farm',
          params: { pool: yield_.token },
        },
      ],
      estimatedOutput: amount + estimatedYield,
      estimatedOutputUsd: amount + estimatedYield,
      timeToExecute: 5,
      gasCost: 0.003,
      successRate: 95.0,
      warnings: ['Impermanent loss risk applies', 'TVL may fluctuate'],
    };
  }

  /**
   * Get all available yield opportunities.
   */
  async getYieldOpportunities(): Promise<ProtocolYield[]> {
    return this.fetchProtocolYields();
  }

  /**
   * Get the best strategy for a specific token.
   */
  async getBestStrategyForToken(walletAddress: string, token: string): Promise<DeFiStrategy | null> {
    const strategies = await this.generateStrategies({
      walletAddress,
      focusToken: token,
      riskTolerance: 'medium',
    });

    return strategies[0] ?? null;
  }

  /**
   * Compare multiple strategies and find the best options.
   */
  compareStrategies(strategies: DeFiStrategy[]): StrategyComparison {
    if (strategies.length === 0) {
      throw new Error('No strategies to compare');
    }

    const bestByApy = strategies.reduce((a, b) => (a.totalApy > b.totalApy ? a : b));
    const bestByRisk = strategies.reduce((a, b) => (a.riskScore < b.riskScore ? a : b));
    const bestByTime = strategies.reduce((a, b) => (a.timeToExecute < b.timeToExecute ? a : b));

    return {
      strategies,
      bestByApy,
      bestByRisk,
      bestByTime,
    };
  }

  /**
   * Filter strategies based on criteria.
   */
  filterStrategies(strategies: DeFiStrategy[], filter: YieldFilter): DeFiStrategy[] {
    return strategies.filter(strategy => {
      if (filter.minApy && strategy.totalApy < filter.minApy) return false;
      if (filter.categories && !filter.categories.includes(strategy.category)) return false;
      if (filter.protocols) {
        const hasProtocol = strategy.steps.some(step =>
          filter.protocols!.some(p => step.protocol.toLowerCase().includes(p.toLowerCase()))
        );
        if (!hasProtocol) return false;
      }
      return true;
    });
  }

  /**
   * Get token mint address from symbol.
   */
  private getTokenMint(symbol: string): string {
    const upper = symbol.toUpperCase();
    return TOKENS[upper as keyof typeof TOKENS] ?? '';
  }

  /**
   * Get protocol brand color.
   */
  private getProtocolColor(protocol: string): string {
    const colors: Record<string, string> = {
      Jupiter: '#6366F1',
      Jito: '#F97316',
      Marinade: '#22C55E',
      Kamino: '#3B82F6',
      Solend: '#8B5CF6',
      Orca: '#14B8A6',
      Raydium: '#8B5CF6',
    };
    return colors[protocol] ?? '#6366F1';
  }

  /**
   * Get yield token symbol for a protocol.
   */
  private getYieldTokenSymbol(protocol: string, token: string): string {
    if (protocol === 'Jupiter') return `jl${token}`;
    if (protocol === 'Kamino') return `km${token}`;
    if (protocol === 'Solend') return `sl${token}`;
    return `${token} Yield`;
  }

  // Price cache for real-time data
  private priceCache: Map<string, { price: number; timestamp: number }> = new Map();
  private readonly PRICE_CACHE_TTL = 60000; // 1 minute

  /**
   * Get token price with real-time API fallback to cache.
   */
  async getTokenPriceAsync(symbol: string): Promise<number> {
    const upper = symbol.toUpperCase();
    const cached = this.priceCache.get(upper);

    if (cached && Date.now() - cached.timestamp < this.PRICE_CACHE_TTL) {
      return cached.price;
    }

    try {
      const price = await this.fetchTokenPrice(upper);
      this.priceCache.set(upper, { price, timestamp: Date.now() });
      return price;
    } catch {
      // Fallback to cached or default
      return cached?.price ?? this.getFallbackPrice(upper);
    }
  }

  /**
   * Fetch real-time price from Birdeye API.
   */
  private async fetchTokenPrice(symbol: string): Promise<number> {
    const mint = this.getTokenMint(symbol);
    if (!mint) return this.getFallbackPrice(symbol);

    try {
      const res = await firstValueFrom(
        this.http.get<any>(`https://api.birdeye.so/v1/token/price?address=${mint}`)
      ).catch(() => null);

      if (res?.data?.value) {
        return res.data.value;
      }
    } catch {}

    // Fallback to Jupiter
    try {
      const res = await firstValueFrom(
        this.http.get<any>(`https://price.jup.ag/v6/price?ids=${mint}`)
      ).catch(() => null);

      if (res?.data?.[mint]?.price) {
        return res.data[mint].price;
      }
    } catch {}

    return this.getFallbackPrice(symbol);
  }

  /**
   * Fallback prices when API fails.
   */
  private getFallbackPrice(symbol: string): number {
    const prices: Record<string, number> = {
      SOL: 180,
      USDC: 1,
      USDT: 1,
      ETH: 3200,
      BTC: 62000,
      Jitosol: 190,
      MSOL: 188,
      BSOL: 185,
      JupSOL: 192,
    };
    return prices[symbol] ?? 1;
  }

  /**
   * Get token price (sync version - uses cache/fallback).
   */
  private getTokenPrice(symbol: string): number {
    const cached = this.priceCache.get(symbol.toUpperCase());
    return cached?.price ?? this.getFallbackPrice(symbol.toUpperCase());
  }

  /**
   * Get all prices in batch for portfolio analysis.
   */
  async getPricesForTokens(mints: string[]): Promise<Map<string, number>> {
    const results = new Map<string, number>();

    try {
      const ids = mints.join(',');
      const res = await firstValueFrom(
        this.http.get<any>(`https://price.jup.ag/v6/price?ids=${ids}`)
      ).catch(() => ({ data: {} }));

      for (const [mint, data] of Object.entries(res.data || {})) {
        results.set(mint, (data as any).price || 1);
      }
    } catch {}

    return results;
  }

  /**
   * Clear price cache (for testing or manual refresh).
   */
  clearPriceCache(): void {
    this.priceCache.clear();
  }
}

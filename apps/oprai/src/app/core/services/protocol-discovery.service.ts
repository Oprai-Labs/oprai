import { Injectable } from '@angular/core';
import { Observable, of, map, catchError } from 'rxjs';

export interface PoolInfo {
  address: string;
  protocol: 'raydium' | 'orca' | 'meteora' | 'jupiter';
  tokenA: string;
  tokenB: string;
  tokenASymbol: string;
  tokenBSymbol: string;
  tvl: number;
  apy: number;
  fee: number;
  // For CLMM pools
  tickSpacing?: number;
  currentTick?: number;
}

export interface TickBounds {
  lower: number;
  upper: number;
  minBinId?: number;
  maxBinId?: number;
}

export interface PoolDiscoveryParams {
  inputMint: string;
  outputMint: string;
  protocol?: 'raydium' | 'orca' | 'meteora' | 'jupiter' | 'all';
}

@Injectable({ providedIn: 'root' })
export class ProtocolDiscoveryService {

  // Cache for pools
  private readonly poolCache = new Map<string, PoolInfo[]>();
  private readonly cacheTimeout = 60_000; // 1 minute

  // Known pool addresses (mainnet)
  readonly KNOWN_POOLS: Record<string, PoolInfo> = {
    // Raydium CLMM - SOL/USDC
    'CRzqx4TVLakh9Lmt3XKH27i2fqQY7D6E4'
      : { address: 'CRzqx4TVLakh9Lmt3XKH27i2fqQY7D6E4', protocol: 'raydium', tokenA: 'So11111111111111111111111111111111111111112', tokenB: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', tokenASymbol: 'SOL', tokenBSymbol: 'USDC', tvl: 50000000, apy: 15.2, fee: 0.25, tickSpacing: 64 },
    // Orca Whirlpool - SOL/USDC
    '85Kq624kYv7SXzJbHxLGdcL4iZigA3K4G'
      : { address: '85Kq624kYv7SXzJbHxLGdcL4iZigA3K4G', protocol: 'orca', tokenA: 'So11111111111111111111111111111111111111112', tokenB: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', tokenASymbol: 'SOL', tokenBSymbol: 'USDC', tvl: 25000000, apy: 12.5, fee: 0.3, tickSpacing: 64 },
    // Meteora DLMM - SOL/USDC
    '5eJxVznvRyyLW2xdq8fJ3hxxT9J3rX4dY'
      : { address: '5eJxVznvRyyLW2xdq8fJ3hxxT9J3rX4dY', protocol: 'meteora', tokenA: 'So11111111111111111111111111111111111111112', tokenB: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', tokenASymbol: 'SOL', tokenBSymbol: 'USDC', tvl: 15000000, apy: 18.7, fee: 0.25 },
    // Jupiter - SOL/USDC (default routing)
    'jup-sOL-USDC': { address: 'jup-sOL-USDC', protocol: 'jupiter', tokenA: 'So11111111111111111111111111111111111111112', tokenB: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', tokenASymbol: 'SOL', tokenBSymbol: 'USDC', tvl: 100000000, apy: 0, fee: 0.1 },
  };

  // Standard token mints
  readonly SOL_MINT = 'So11111111111111111111111111111111111111112';
  readonly USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';
  readonly USDT_MINT = 'Es9vMFrzaCERmB3CCH4FUo4gGXwU8sLRrMG7c4ETi6V';

  /**
   * Discover pools for a token pair.
   * Searches across Raydium, Orca, Meteora, and Jupiter.
   */
  discoverPools(params: PoolDiscoveryParams): Observable<PoolInfo[]> {
    const cacheKey = `${params.inputMint}-${params.outputMint}-${params.protocol ?? 'all'}`;

    // Check cache
    const cached = this.poolCache.get(cacheKey);
    if (cached && Date.now() - this.cacheTimeout < this.cacheTimeout) {
      return of(cached);
    }

    // For now, return known pools filtered by token pair
    // In production, this would call APIs for each protocol
    const pools = this.findKnownPools(params.inputMint, params.outputMint, params.protocol);

    if (pools.length > 0) {
      this.poolCache.set(cacheKey, pools);
    }

    return of(pools);
  }

  /**
   * Find known pools for a token pair.
   * This is a simplified version - in production, you'd query the blockchain.
   */
  private findKnownPools(
    inputMint: string,
    outputMint: string,
    protocol?: 'raydium' | 'orca' | 'meteora' | 'jupiter' | 'all'
  ): PoolInfo[] {
    const normalizedInput = this.normalizeMint(inputMint);
    const normalizedOutput = this.normalizeMint(outputMint);

    const pools: PoolInfo[] = [];

    for (const pool of Object.values(this.KNOWN_POOLS)) {
      const poolTokenA = this.normalizeMint(pool.tokenA);
      const poolTokenB = this.normalizeMint(pool.tokenB);

      // Check if this pool matches the token pair (in either direction)
      const matches = (poolTokenA === normalizedInput && poolTokenB === normalizedOutput) ||
                      (poolTokenA === normalizedOutput && poolTokenB === normalizedInput);

      if (!matches) continue;

      // Filter by protocol if specified
      if (protocol && protocol !== 'all' && pool.protocol !== protocol) continue;

      pools.push({
        ...pool,
        // Reverse token order if needed to match input/output
        tokenA: poolTokenA === normalizedInput ? pool.tokenA : pool.tokenB,
        tokenB: poolTokenB === normalizedOutput ? pool.tokenB : pool.tokenA,
        tokenASymbol: poolTokenA === normalizedInput ? pool.tokenASymbol : pool.tokenBSymbol,
        tokenBSymbol: poolTokenB === normalizedOutput ? pool.tokenBSymbol : pool.tokenASymbol,
      });
    }

    // Sort by TVL (highest first)
    return pools.sort((a, b) => b.tvl - a.tvl);
  }

  /**
   * Normalize a mint address (handle 'SOL' shorthand).
   */
  private normalizeMint(mint: string): string {
    if (!mint) return '';
    if (mint.toUpperCase() === 'SOL') return this.SOL_MINT;
    if (mint.toUpperCase() === 'USDC') return this.USDC_MINT;
    if (mint.toUpperCase() === 'USDT') return this.USDT_MINT;
    return mint;
  }

  /**
   * Calculate optimal tick bounds for a CLMM pool.
   * @param pool The pool to calculate bounds for
   * @param priceRangePercent The price range as percentage (e.g., 10 = 10% range)
   * @param currentPrice Current price of the pool (tokenA/tokenB)
   */
  calculateTickBounds(pool: PoolInfo, priceRangePercent: number, currentPrice?: number): TickBounds {
    if (pool.protocol === 'meteora') {
      // Meteora uses bin IDs
      return this.calculateMeteoraBinBounds(pool, priceRangePercent);
    }

    // Raydium and Orca use tick-based ranges
    const tickSpacing = pool.tickSpacing ?? 64;
    const rangeMultiplier = priceRangePercent / 100;

    // Default price if not provided (assume 1 SOL = $100 for SOL pairs)
    const price = currentPrice ?? this.estimatePrice(pool);

    // Calculate price boundaries
    const lowerPrice = price * (1 - rangeMultiplier);
    const upperPrice = price * (1 + rangeMultiplier);

    // Convert price to tick (using log base 1.0001 for concentrated liquidity)
    // tick = log(price) / log(1.0001)
    const lowerTick = this.priceToTick(lowerPrice, tickSpacing);
    const upperTick = this.priceToTick(upperPrice, tickSpacing);

    return {
      lower: lowerTick,
      upper: upperTick,
    };
  }

  /**
   * Calculate bin bounds for Meteora DLMM pools.
   */
  private calculateMeteoraBinBounds(_pool: PoolInfo, priceRangePercent: number): TickBounds {
    // For Meteora, we use bin IDs instead of ticks
    // Each bin represents a price range
    // Default to a reasonable range (e.g., 20 bins on each side)
    const binRange = Math.round(priceRangePercent / 2);

    // Assume middle bin is around 0 for now
    // In production, you'd fetch the current active bin
    const activeBin = 0;

    return {
      lower: activeBin - binRange,
      upper: activeBin + binRange,
      minBinId: activeBin - binRange,
      maxBinId: activeBin + binRange,
    };
  }

  /**
   * Convert price to tick.
   */
  private priceToTick(price: number, tickSpacing: number): number {
    // tick = log(price) / log(1.0001)
    const tick = Math.log(price) / Math.log(1.0001);
    // Round to nearest tick spacing
    return Math.round(tick / tickSpacing) * tickSpacing;
  }

  /**
   * Estimate price for a pool based on known values.
   */
  private estimatePrice(pool: PoolInfo): number {
    // Simple estimation for common pairs
    if (pool.tokenASymbol === 'SOL' && pool.tokenBSymbol === 'USDC') return 100;
    if (pool.tokenBSymbol === 'SOL' && pool.tokenASymbol === 'USDC') return 0.01;
    return 1; // Default
  }

  /**
   * Get auto-fill values for an action based on the selected pool.
   */
  getAutoFillValues(
    actionType: string,
    pool: PoolInfo,
    amount: number
  ): Record<string, string> {
    const values: Record<string, string> = {
      pool: pool.address,
    };

    switch (actionType) {
      case 'raydium_add_liquidity':
      case 'raydium_open_position': {
        const raydiumBounds = this.calculateTickBounds(pool, 20); // 20% range
        values['tickLower'] = raydiumBounds.lower.toString();
        values['tickUpper'] = raydiumBounds.upper.toString();
        values['inputMint'] = pool.tokenA;
        values['inputAmount'] = (amount / 2).toString(); // Split amount between tokens
        break;
      }

      case 'orca_add_liquidity':
      case 'orca_open_position': {
        const orcaBounds = this.calculateTickBounds(pool, 20);
        values['whirlpool'] = pool.address;
        values['tickLower'] = orcaBounds.lower.toString();
        values['tickUpper'] = orcaBounds.upper.toString();
        values['amountA'] = (amount / 2).toString();
        values['amountB'] = (amount / 2).toString();
        break;
      }

      case 'meteora_add_liquidity':
      case 'meteora_open_position': {
        const meteoraBounds = this.calculateTickBounds(pool, 20);
        values['pool'] = pool.address;
        values['minBinId'] = meteoraBounds.minBinId?.toString() ?? '-10';
        values['maxBinId'] = meteoraBounds.maxBinId?.toString() ?? '10';
        values['amountX'] = (amount / 2).toString();
        values['amountY'] = (amount / 2).toString();
        break;
      }

      case 'raydium_swap':
      case 'orca_swap':
      case 'meteora_swap':
        values['inputMint'] = pool.tokenA;
        values['outputMint'] = pool.tokenB;
        break;
    }

    return values;
  }

  /**
   * Get the best pool for a token pair (highest TVL).
   */
  getBestPool(inputMint: string, outputMint: string): Observable<PoolInfo | null> {
    return this.discoverPools({ inputMint, outputMint }).pipe(
      map(pools => pools.length > 0 ? pools[0] : null),
      catchError(() => of(null))
    );
  }

  /**
   * Get protocol-specific API endpoints.
   */
  getProtocolApiUrl(protocol: 'raydium' | 'orca' | 'meteora'): string {
    switch (protocol) {
      case 'raydium': return 'https://api.raydium.io/v2';
      case 'orca': return 'https://api.orca.so';
      case 'meteora': return 'https://dlmm-api.meteora.ag';
      default: return '';
    }
  }
}

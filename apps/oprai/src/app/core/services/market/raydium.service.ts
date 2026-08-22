import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Raydium AMM Pool info */
export interface RaydiumPool {
  id: string;
  mintA: string;
  mintB: string;
  mintDecimalsA: number;
  mintDecimalsB: number;
  vaultA: string;
  vaultB: string;
  lpMint: string;
  lpDecimals: number;
  tvl: number;
  volume24h: number;
  feeRate: number;
  apr: number;
  name?: string;
}

/** CLMM (Concentrated Liquidity) Pool info */
export interface RaydiumClmmPool {
  id: string;
  mintA: string;
  mintB: string;
  mintDecimalsA: number;
  mintDecimalsB: number;
  currentPrice: number;
  currentTick: number;
  feeRate: number;
  tvl: number;
  volume24h: number;
  apr: number;
}

/** CLMM Position info */
export interface RaydiumClmmPosition {
  nftMint: string;
  poolId: string;
  tickLower: number;
  tickUpper: number;
  liquidity: string;
  tokenA: string;
  tokenB: string;
  feeA: string;
  feeB: string;
  inRange: boolean;
}

/** Swap quote from Raydium compute API */
export interface RaydiumSwapQuote {
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;
  priceImpactPct: string;
  routePlan: Array<{
    poolId: string;
    inputMint: string;
    outputMint: string;
    inAmount: string;
    outAmount: string;
    percent: number;
  }>;
}

/** Build response from backend */
export interface RaydiumBuildResponse {
  preview: {
    id: string;
    type: string;
    description: string;
    estimatedFee: string;
    params: Record<string, unknown>;
    warnings: string[];
    requiresApproval: boolean;
  };
  transaction: string; // base64-encoded V0 versioned transaction
}

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const RAYDIUM_API = 'https://api.raydium.io/v2';

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class RaydiumService {
  private readonly http = inject(HttpClient);

  // ─── Pool Queries ────────────────────────────────────────────────────────────

  /**
   * Fetch CLMM pools from Raydium API.
   * GET /v2/ammV3/ammPools → { data: [...] }
   * Fields per pool: id, mintA, mintB, mintDecimalsA, mintDecimalsB, tvl,
   *   price, day.volume, day.apr, day.feeApr, ammConfig.tradeFeeRate
   */
  async getAmmPools(): Promise<RaydiumPool[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any>(`${RAYDIUM_API}/ammV3/ammPools`)
      );
      const raw: any[] = resp?.data ?? [];
      return raw.map(p => ({
        id: p.id,
        mintA: p.mintA,
        mintB: p.mintB,
        mintDecimalsA: p.mintDecimalsA ?? 9,
        mintDecimalsB: p.mintDecimalsB ?? 6,
        vaultA: p.vaultA ?? '',
        vaultB: p.vaultB ?? '',
        lpMint: '',
        lpDecimals: 0,
        tvl: parseFloat(p.tvl ?? '0'),
        volume24h: parseFloat(p.day?.volume ?? '0'),
        feeRate: (p.ammConfig?.tradeFeeRate ?? 0) / 1e6,
        apr: parseFloat(p.day?.apr ?? '0'),
      }));
    } catch (err) {
      console.error('Failed to fetch Raydium AMM pools:', err);
      return [];
    }
  }

  /** Fetch all CLMM pools (alias for getAmmPools — same endpoint). */
  async getClmmPools(): Promise<RaydiumClmmPool[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any>(`${RAYDIUM_API}/ammV3/ammPools`)
      );
      const raw: any[] = resp?.data ?? [];
      return raw.map(p => ({
        id: p.id,
        mintA: p.mintA,
        mintB: p.mintB,
        mintDecimalsA: p.mintDecimalsA ?? 9,
        mintDecimalsB: p.mintDecimalsB ?? 6,
        currentPrice: parseFloat(p.price ?? '0'),
        currentTick: 0,
        feeRate: (p.ammConfig?.tradeFeeRate ?? 0) / 1e6,
        tvl: parseFloat(p.tvl ?? '0'),
        volume24h: parseFloat(p.day?.volume ?? '0'),
        apr: parseFloat(p.day?.apr ?? '0'),
      }));
    } catch (err) {
      console.error('Failed to fetch Raydium CLMM pools:', err);
      return [];
    }
  }

  /** Get pool info by ID. */
  async getPoolInfo(poolId: string): Promise<RaydiumPool | null> {
    const pools = await this.getAmmPools();
    return pools.find(p => p.id === poolId) ?? null;
  }

  /** Get CLMM pool info by ID. */
  async getClmmPoolInfo(poolId: string): Promise<RaydiumClmmPool | null> {
    const pools = await this.getClmmPools();
    return pools.find(p => p.id === poolId) ?? null;
  }

  /** Get user CLMM positions.
   *
   * `api.raydium.io/v2/positions` does not exist (404) — Raydium has no public
   * positions-by-owner REST endpoint; CLMM positions are position-NFTs read
   * on-chain. The direct browser call only 404-spammed the console every poll
   * and always returned nothing. Return empty until this is wired to an on-chain
   * read via the backend (a `raydium_get_user_positions` action exists). */
  async getClmmPositions(_walletAddress: string): Promise<RaydiumClmmPosition[]> {
    return [];
  }

  // ─── Swap Quote ──────────────────────────────────────────────────────────────

  /** Get a swap quote from Raydium compute API. */
  async getSwapQuote(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps = 50
  ): Promise<RaydiumSwapQuote | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<RaydiumSwapQuote>(`${RAYDIUM_API}/compute/swap-base-in`, {
          inputMint,
          outputMint,
          amount,
          slippageBps,
          txVersion: 'V0',
        })
      );
      return resp;
    } catch (err) {
      console.error('Raydium swap quote error:', err);
      return null;
    }
  }

  // ─── Transaction Builders (via Backend) ──────────────────────────────────────

  /** Build a Raydium swap transaction via backend. */
  async buildSwap(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps = 50
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_swap', {
      inputMint,
      outputMint,
      amount,
      slippageBps,
    });
  }

  /** Build add liquidity transaction. */
  async buildAddLiquidity(
    poolId: string,
    amount: string,
    inputMint?: string,
    baseIn = true,
    slippageBps = 100
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_add_liquidity', {
      poolId,
      amount,
      inputMint,
      baseIn,
      slippageBps,
    });
  }

  /** Build remove liquidity transaction. */
  async buildRemoveLiquidity(
    poolId: string,
    lpAmount: string,
    slippageBps = 100
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_remove_liquidity', {
      poolId,
      lpAmount,
      slippageBps,
    });
  }

  /** Build create pool transaction. */
  async buildCreatePool(
    mintA: string,
    mintB: string,
    amountA: string,
    amountB: string,
    startTime = 0
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_create_pool', {
      mintA,
      mintB,
      amountA,
      amountB,
      startTime,
    });
  }

  /** Build open CLMM position transaction. */
  async buildOpenPosition(
    poolId: string,
    inputMint: string,
    inputAmount: string,
    tickLower: number,
    tickUpper: number,
    slippageBps = 100
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_open_position', {
      poolId,
      inputMint,
      inputAmount,
      tickLower,
      tickUpper,
      slippageBps,
    });
  }

  /** Build close CLMM position transaction. */
  async buildClosePosition(
    positionId: string
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_close_position', {
      positionId,
    });
  }

  /** Build increase CLMM position transaction. */
  async buildIncreasePosition(
    positionId: string,
    inputMint: string,
    inputAmount: string,
    slippageBps = 100
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_increase_position', {
      positionId,
      inputMint,
      inputAmount,
      slippageBps,
    });
  }

  /** Build decrease CLMM position transaction. */
  async buildDecreasePosition(
    positionId: string,
    liquidity: string,
    slippageBps = 100
  ): Promise<RaydiumBuildResponse | null> {
    return this.buildAction('raydium_decrease_position', {
      positionId,
      liquidity,
      slippageBps,
    });
  }

  // ─── Private Helpers ────────────────────────────────────────────────────────

  private async buildAction(
    type: string,
    params: Record<string, unknown>
  ): Promise<RaydiumBuildResponse | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<RaydiumBuildResponse>(
          `${environment.apiBase}/solana/actions/build`,
          { type, params }
        )
      );
      return resp;
    } catch (err) {
      console.error(`Raydium build ${type} error:`, err);
      return null;
    }
  }
}

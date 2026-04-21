import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Orca Whirlpool info */
export interface OrcaWhirlpool {
  address: string;
  mintA: string;
  mintB: string;
  mintDecimalsA: number;
  mintDecimalsB: number;
  currentPrice: number;
  currentTickIndex: number;
  feeRate: number;
  tvl: number;
  volume24h: number;
  apr: number;
  rewardInfos: OrcaRewardInfo[];
}

/** Reward info for a Whirlpool */
export interface OrcaRewardInfo {
  mint: string;
  vault: string;
  authority: string;
  decimals: number;
  growthGlobal: string;
}

/** Whirlpool Position info */
export interface OrcaPosition {
  address: string;
  whirlpool: string;
  tickLowerIndex: number;
  tickUpperIndex: number;
  liquidity: string;
  tokenOwnedA: string;
  tokenOwnedB: string;
  feeOwnedA: string;
  feeOwnedB: string;
  rewardInfos: OrcaPositionRewardInfo[];
  inRange: boolean;
}

/** Position reward info */
export interface OrcaPositionRewardInfo {
  growthInsideCheckpoint: string;
  amountOwed: string;
  pendingRewards: string;
}

/** Swap quote from Orca API */
export interface OrcaSwapQuote {
  inputMint: string;
  outputMint: string;
  inAmount: string;
  estimatedAmountOut: string;
  priceImpact: number;
  minAmountOut: string;
  route: OrcaRouteHop[];
}

/** Route hop for swap */
export interface OrcaRouteHop {
  whirlpool: string;
  inputMint: string;
  outputMint: string;
  inAmount: string;
  outAmount: string;
  percent: number;
}

/** Build response from backend */
export interface OrcaBuildResponse {
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

const ORCA_WHIRLPOOLS_API = 'https://api.orca.so/v1/whirlpool';

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class OrcaService {
  private readonly http = inject(HttpClient);

  // ─── Whirlpool Queries ────────────────────────────────────────────────────────

  /** Fetch all Whirlpools (concentrated liquidity pools). */
  async getWhirlpools(): Promise<OrcaWhirlpool[]> {
    try {
      // GET /v1/whirlpool/list → { whirlpools: [...] }
      // Each item: { address, tokenA: {mint, symbol, decimals}, tokenB: {...},
      //   tickSpacing, price, lpFeeRate, tvl, volume: {day,week,month},
      //   feeApr, reward0Apr, reward1Apr, reward2Apr, totalApr }
      const resp = await firstValueFrom(
        this.http.get<any>(`${ORCA_WHIRLPOOLS_API}/list`)
      );
      const raw: any[] = resp?.whirlpools ?? [];
      return raw.map(w => ({
        address: w.address,
        mintA: w.tokenA?.mint ?? '',
        mintB: w.tokenB?.mint ?? '',
        mintDecimalsA: w.tokenA?.decimals ?? 9,
        mintDecimalsB: w.tokenB?.decimals ?? 6,
        currentPrice: w.price ?? 0,
        currentTickIndex: 0,
        feeRate: w.lpFeeRate ?? 0,
        tvl: w.tvl ?? 0,
        volume24h: w.volume?.day ?? 0,
        apr: w.totalApr ?? w.feeApr ?? 0,
        rewardInfos: [],
        // extra fields for display
        symbolA: w.tokenA?.symbol ?? '',
        symbolB: w.tokenB?.symbol ?? '',
        tickSpacing: w.tickSpacing ?? 0,
        feeApr: w.feeApr ?? 0,
        reward0Apr: w.reward0Apr ?? 0,
      } as any));
    } catch (err) {
      console.error('Failed to fetch Orca Whirlpools:', err);
      return [];
    }
  }

  /** Get Whirlpool info by address. */
  async getWhirlpool(address: string): Promise<OrcaWhirlpool | null> {
    try {
      const all = await this.getWhirlpools();
      return all.find(w => w.address === address) ?? null;
    } catch (err) {
      console.error('Failed to fetch Whirlpool:', err);
      return null;
    }
  }

  /** Get Whirlpools by token pair. */
  async getWhirlpoolsByPair(
    mintA: string,
    mintB: string
  ): Promise<OrcaWhirlpool[]> {
    try {
      const all = await this.getWhirlpools();
      return all.filter(w =>
        (w.mintA === mintA && w.mintB === mintB) ||
        (w.mintA === mintB && w.mintB === mintA)
      );
    } catch (err) {
      console.error('Failed to fetch Whirlpools by pair:', err);
      return [];
    }
  }

  /** Get user positions. */
  async getPositions(walletAddress: string): Promise<OrcaPosition[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any>(
          `${ORCA_WHIRLPOOLS_API}/positions?owner=${walletAddress}`
        )
      );
      const raw: any[] = Array.isArray(resp) ? resp : resp?.positions ?? [];
      return raw.map(p => ({
        address: p.address ?? p.positionMint ?? '',
        whirlpool: p.whirlpool ?? '',
        tickLowerIndex: p.tickLowerIndex ?? 0,
        tickUpperIndex: p.tickUpperIndex ?? 0,
        liquidity: p.liquidity ?? '0',
        tokenOwnedA: p.tokenOwnedA ?? '0',
        tokenOwnedB: p.tokenOwnedB ?? '0',
        feeOwnedA: p.feeOwnedA ?? '0',
        feeOwnedB: p.feeOwnedB ?? '0',
        rewardInfos: p.rewardInfos ?? [],
        inRange: p.inRange ?? false,
      }));
    } catch (err) {
      console.error('Failed to fetch Orca positions:', err);
      return [];
    }
  }

  // ─── Swap Quote ──────────────────────────────────────────────────────────────

  /** Get a swap quote from Orca API. */
  async getSwapQuote(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps = 50,
    swapMode: 'in' | 'out' = 'in'
  ): Promise<OrcaSwapQuote | null> {
    try {
      const mode = swapMode === 'in' ? 'quote' : 'quote-out';
      const resp = await firstValueFrom(
        this.http.get<OrcaSwapQuote>(
          `${ORCA_WHIRLPOOLS_API}/${mode}?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amount}&slippageBps=${slippageBps}`
        )
      );
      return resp;
    } catch (err) {
      console.error('Orca swap quote error:', err);
      return null;
    }
  }

  // ─── Transaction Builders (via Backend) ──────────────────────────────────────

  /** Build an Orca swap transaction via backend. */
  async buildSwap(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps = 50,
    swapMode: 'in' | 'out' = 'in',
    whirlpool?: string
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_swap', {
      inputMint,
      outputMint,
      amount,
      slippageBps,
      swapMode,
      whirlpool,
    });
  }

  /** Build add liquidity transaction. */
  async buildAddLiquidity(
    whirlpool: string,
    amountA: string,
    amountB: string,
    slippageBps = 100
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_add_liquidity', {
      whirlpool,
      amountA,
      amountB,
      slippageBps,
    });
  }

  /** Build remove liquidity transaction. */
  async buildRemoveLiquidity(
    whirlpool: string,
    liquidity: string,
    slippageBps = 100
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_remove_liquidity', {
      whirlpool,
      liquidity,
      slippageBps,
    });
  }

  /** Build open Whirlpool position transaction. */
  async buildOpenPosition(
    whirlpool: string,
    inputMint: string,
    inputAmount: string,
    tickLower: number,
    tickUpper: number,
    slippageBps = 100
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_open_position', {
      whirlpool,
      inputMint,
      inputAmount,
      tickLower,
      tickUpper,
      slippageBps,
    });
  }

  /** Build close Whirlpool position transaction. */
  async buildClosePosition(
    position: string
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_close_position', {
      position,
    });
  }

  /** Build increase Whirlpool position transaction. */
  async buildIncreasePosition(
    position: string,
    inputMint: string,
    inputAmount: string,
    slippageBps = 100
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_increase_position', {
      position,
      inputMint,
      inputAmount,
      slippageBps,
    });
  }

  /** Build decrease Whirlpool position transaction. */
  async buildDecreasePosition(
    position: string,
    liquidity: string,
    slippageBps = 100
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_decrease_position', {
      position,
      liquidity,
      slippageBps,
    });
  }

  /** Build collect fees transaction. */
  async buildCollectFees(
    position: string
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_collect_fees', {
      position,
    });
  }

  /** Build collect rewards transaction. */
  async buildCollectRewards(
    position: string,
    rewardIndex = 0
  ): Promise<OrcaBuildResponse | null> {
    return this.buildAction('orca_collect_rewards', {
      position,
      rewardIndex,
    });
  }

  // ─── Private Helpers ────────────────────────────────────────────────────────

  private async buildAction(
    type: string,
    params: Record<string, unknown>
  ): Promise<OrcaBuildResponse | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<OrcaBuildResponse>(
          `${environment.apiBase}/solana/actions/build`,
          { type, params }
        )
      );
      return resp;
    } catch (err) {
      console.error(`Orca build ${type} error:`, err);
      return null;
    }
  }
}

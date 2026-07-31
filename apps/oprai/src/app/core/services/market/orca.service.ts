import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

// ──────────────────────────────────────────────────────────────────────────────
// Gateway-backed reads (Orca v2 API via solana-service)
//
// The browser-side methods below this block talk to Orca's public v1 API
// directly. These go through our own backend instead, which is what the DLMM
// and DAMM v2 cards do: one API version, one place the payload shape is
// pinned, and no third-party host in the browser's request path.
// ──────────────────────────────────────────────────────────────────────────────

/** One row of `orca_get_pools` — Orca v2 `/pools` shape. */
export interface OrcaPoolRow {
  address: string;
  tokenA: { address: string; symbol: string; decimals: number; imageUrl?: string };
  tokenB: { address: string; symbol: string; decimals: number; imageUrl?: string };
  /** Hundredths of a basis point: 400 = 0.04%. */
  feeRate: number;
  tickSpacing: number;
  price: number;
  tvlUsdc: string | number;
  /** Fees over TVL for the period — the closest thing Orca gives to an APR. */
  yieldOverTvl: string | number;
  stats?: Record<string, { volume?: string; fees?: string }>;
  hasWarning?: boolean;
  lockedLiquidityPercent?: string | number;
}

export interface OrcaPoolsPage {
  rows: OrcaPoolRow[];
  nextCursor: string | null;
  prevCursor: string | null;
}

/** One row of `orca_get_user_positions`. */
export interface OrcaUserPosition {
  type: 'position' | 'bundle';
  positionAddress: string;
  positionMint: string;
  whirlpool: string;
  liquidity: string;
  tickLowerIndex: number;
  tickUpperIndex: number;
  /** Human price (token B per token A), decimal-adjusted by the backend. */
  priceLower: number;
  priceUpper: number;
  currentPrice?: number;
  inRange?: boolean;
  /** What the position holds right now, derived from liquidity + ticks. */
  amountA?: number;
  amountB?: number;
  tokenAMint?: string;
  tokenBMint?: string;
  tokenASymbol?: string | null;
  tokenBSymbol?: string | null;
  tokenADecimals?: number;
  tokenBDecimals?: number;
  feeOwedA: number | string;
  feeOwedB: number | string;
  feeOwedAUi?: number;
  feeOwedBUi?: number;
}

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

  // ─── Gateway-backed reads ───────────────────────────────────────────────────

  /**
   * Whirlpool list from Orca's v2 API, proxied by our backend.
   *
   * Orca pages by CURSOR, not page number — the response carries the cursor
   * for the next page, so a card can only move forward and back one step at a
   * time. Passing a page index would be inventing an API that isn't there.
   */
  async fetchPoolsPage(opts: {
    sortBy?: string;
    sortDirection?: 'asc' | 'desc';
    size?: number;
    next?: string;
    previous?: string;
    token?: string;
    /** Asset category, e.g. 'rwa' | 'stablecoin' | 'lst' | 'memecoin'. */
    category?: string;
    /** Skip pools below this USD TVL — most of Orca's pools are dust. */
    minTvl?: number;
  } = {}): Promise<OrcaPoolsPage | null> {
    const params: Record<string, unknown> = {};
    if (opts.sortBy)        params['sortBy']        = opts.sortBy;
    if (opts.sortDirection) params['sortDirection'] = opts.sortDirection;
    if (opts.size)          params['size']          = opts.size;
    if (opts.next)          params['next']          = opts.next;
    if (opts.previous)      params['previous']      = opts.previous;
    if (opts.token)         params['token']         = opts.token;
    if (opts.category)      params['category']      = opts.category;
    if (opts.minTvl)        params['minTvl']        = opts.minTvl;
    try {
      const resp = await firstValueFrom(
        this.http.post<{ data?: { data?: OrcaPoolRow[]; meta?: { cursor?: { next?: string; previous?: string } } } }>(
          `${environment.apiBase}/actions/build`,
          { type: 'orca_get_pools', params },
          { withCredentials: true },
        ),
      );
      const payload = resp?.data;
      if (!payload) return null;
      return {
        rows: payload.data ?? [],
        nextCursor: payload.meta?.cursor?.next ?? null,
        prevCursor: payload.meta?.cursor?.previous ?? null,
      };
    } catch (err) {
      console.error('Failed to fetch Orca pools:', err);
      return null;
    }
  }

  /** Free-text pool search (symbol, name or address), optionally kept inside
   *  the asset category the user is browsing. */
  async searchPoolsPage(
    q: string,
    opts: { size?: number; next?: string; category?: string } = {},
  ): Promise<OrcaPoolsPage | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<{ data?: { data?: OrcaPoolRow[]; meta?: { cursor?: { next?: string; previous?: string } } } }>(
          `${environment.apiBase}/actions/build`,
          {
            type: 'orca_search_pools',
            params: {
              q,
              ...(opts.size ? { size: opts.size } : {}),
              ...(opts.next ? { next: opts.next } : {}),
              ...(opts.category ? { category: opts.category } : {}),
            },
          },
          { withCredentials: true },
        ),
      );
      const payload = resp?.data;
      if (!payload) return null;
      return {
        rows: payload.data ?? [],
        nextCursor: payload.meta?.cursor?.next ?? null,
        prevCursor: payload.meta?.cursor?.previous ?? null,
      };
    } catch (err) {
      console.error('Failed to search Orca pools:', err);
      return null;
    }
  }

  /**
   * Price a swap through Orca's own program.
   *
   * The build path uses orca_whirlpools' `swap_instructions`, which quotes
   * from the pool's tick arrays — so previewing through Jupiter (even
   * restricted to Whirlpool venues) would show a number the transaction is
   * not obliged to honour. Same source for the preview and the execution.
   *
   * Returns base units, matching the shape the swap card already consumes.
   */
  async quoteSwap(
    inputMint: string,
    outputMint: string,
    amount: string,
    swapMode: 'ExactIn' | 'ExactOut' = 'ExactIn',
    slippageBps = 50,
  ): Promise<{ inAmount: string; outAmount: string; priceImpactPct: string } | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<{ quote?: { inAmount: number; outAmount: number } }>(
          `${environment.apiBase}/actions/build`,
          {
            type: 'orca_swap',
            params: {
              inputMint, outputMint, amount,
              swapMode: swapMode === 'ExactOut' ? 'out' : 'in',
              slippageBps,
            },
          },
          { withCredentials: true },
        ),
      );
      const q = resp?.quote;
      if (!q) return null;
      // The card works in base units; the builder reports human units.
      const dec = await this.decimalsFor(inputMint, outputMint);
      return {
        inAmount: String(Math.round(q.inAmount * 10 ** dec.input)),
        outAmount: String(Math.round(q.outAmount * 10 ** dec.output)),
        priceImpactPct: '0',
      };
    } catch (err) {
      console.error('Orca quote error:', err);
      return null;
    }
  }

  /** Decimals for a mint pair, from the pool list we already cache upstream. */
  private async decimalsFor(a: string, b: string): Promise<{ input: number; output: number }> {
    const page = await this.fetchPoolsPage({ token: a, size: 20 });
    const row = page?.rows.find(r =>
      (r.tokenA.address === a && r.tokenB.address === b) ||
      (r.tokenA.address === b && r.tokenB.address === a));
    if (!row) return { input: 9, output: 9 };
    return row.tokenA.address === a
      ? { input: row.tokenA.decimals, output: row.tokenB.decimals }
      : { input: row.tokenB.decimals, output: row.tokenA.decimals };
  }

  /** The connected wallet's Whirlpool positions, read on-chain by the backend. */
  async fetchUserPositions(): Promise<OrcaUserPosition[] | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<{ data?: { positions?: OrcaUserPosition[] } }>(
          `${environment.apiBase}/actions/build`,
          { type: 'orca_get_user_positions', params: {} },
          { withCredentials: true },
        ),
      );
      return resp?.data?.positions ?? null;
    } catch (err) {
      console.error('Failed to fetch Orca positions:', err);
      return null;
    }
  }

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

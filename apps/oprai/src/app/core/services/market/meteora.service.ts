import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/** Meteora DLMM Pool info */
export interface MeteoraPool {
  address: string;
  tokenXMint: string;
  tokenYMint: string;
  tokenXSymbol: string;
  tokenYSymbol: string;
  tokenXDecimals: number;
  tokenYDecimals: number;
  currentPrice: number;
  baseFee: number;
  tvl: number;
  volume24h: number;
  apr: number;
  binStep: number;
  activeBinId: number;
}

/** Meteora Position info */
export interface MeteoraPosition {
  address: string;
  pool: string;
  owner: string;
  lowerBinId: number;
  upperBinId: number;
  totalTokenXAmount: string;
  totalTokenYAmount: string;
  feeX: string;
  feeY: string;
  inRange: boolean;
  liquidityUsd: number;
}

/** Meteora Farm info */
export interface MeteoraFarm {
  address: string;
  pool: string;
  rewardMint: string;
  rewardSymbol: string;
  apr: number;
  totalStaked: string;
}

/** Build response from backend */
export interface MeteoraBuildResponse {
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

/** Single row returned by meteora_dlmm_get_pairs (datapi.meteora.ag). */
export interface DlmmPair {
  address: string;
  name: string;                       // "JupSOL-INF"
  apr: number;                        // already in percent
  apy: number;                        // already in percent
  tvl: number;                        // USD
  current_price: number;
  cumulative_metrics?: { fees: number; volume: number };
  fees: Record<string, number>;       // keys: 1h, 2h, 4h, 12h, 24h, 30m
  volume: Record<string, number>;
  fee_tvl_ratio: Record<string, number>;
  pool_config: {
    base_fee_pct: number;
    bin_step: number;
    max_fee_pct: number;
    protocol_fee_pct: number;
  };
  token_x: { address: string; symbol: string; decimals: number; price: number };
  token_y: { address: string; symbol: string; decimals: number; price: number };
  has_farm: boolean;
  is_blacklisted: boolean;
  launchpad?: string;
  tags?: string[];
}

export interface DlmmPairsPage {
  current_page: number;     // 1-based
  page_size: number;
  pages: number;
  total: number;
  data: DlmmPair[];
}

/**
 * Single row returned by `meteora_dammv2_get_pools` (damm-v2.datapi.meteora.ag).
 * Constant-product AMM with optional concentrated-liquidity flag — the
 * reserves on each side are first-class, so the ratio engine can compute
 * deposits without a second RPC.
 */
export interface DammV2Pool {
  address: string;
  name: string;                 // "SOL-USDC"
  token_x: { address: string; symbol: string; decimals: number; price: number };
  token_y: { address: string; symbol: string; decimals: number; price: number };
  token_x_amount: number;       // human-units reserve (X side)
  token_y_amount: number;       // human-units reserve (Y side)
  current_price: number;        // Y per X
  tvl: number;
  has_farm: boolean;
  farm_apr: number;
  farm_apy: number;
  permanent_lock_liquidity?: number;
  pool_config: {
    base_fee_pct: number;
    protocol_fee_pct: number;
    concentrated_liquidity: boolean;
    pool_type: number;
    activation_type: number;
    activation_point: number;
  };
  volume?: Record<string, number>;     // 30m / 1h / 24h / 7d …
  fees?: Record<string, number>;
  fee_tvl_ratio?: Record<string, number>;
}

export interface DammV2PoolsPage {
  current_page: number;
  page_size: number;
  pages: number;
  total: number;
  data: DammV2Pool[];
}

/**
 * Single row returned by `meteora_dammv1_get_pools` (amm.meteora.ag/pools).
 * Legacy AMM endpoint — flat array, snake_case keys, paired token reserves
 * delivered as parallel arrays (`pool_token_mints[i]`, `pool_token_amounts[i]`).
 */
export interface DammV1Pool {
  pool_address: string;
  pool_name: string;             // "Bonk-USDC"
  pool_token_mints: string[];    // [mintA, mintB]
  pool_token_amounts: string[];  // raw integer reserves as strings
  pool_token_usd_amounts: string[];
  lp_mint: string;
  pool_tvl: string;
  daily_base_apy: string;
  weekly_base_apy: string;
  total_fee_pct: string;
  pool_version: number;
  pool_lp_price_in_usd: string;
  trading_volume: number;
  weekly_trading_volume: number;
  is_lst: boolean;
  is_forex: boolean;
}

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const METEORA_API = 'https://dlmm-api.meteora.ag';

// Meteora DLMM Program ID
export const METEORA_DLMM_PROGRAM_ID = 'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo';

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class MeteoraService {
  private readonly http = inject(HttpClient);

  // ─── Pool Queries ────────────────────────────────────────────────────────────

  /**
   * Get all Meteora DLMM pools.
   * GET /pair/all → array of pairs with fields:
   *   address, name, mint_x, mint_y, bin_step, base_fee_percentage,
   *   liquidity, fees_24h, trade_volume_24h, current_price, apr, apy
   */
  async getPools(): Promise<MeteoraPool[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${METEORA_API}/pair/all`)
      );
      const raw: any[] = resp ?? [];
      return raw.map(p => {
        const [symX, symY] = (p.name ?? '-').split('-');
        return {
          address: p.address,
          tokenXMint: p.mint_x ?? '',
          tokenYMint: p.mint_y ?? '',
          tokenXSymbol: symX ?? '',
          tokenYSymbol: symY ?? '',
          tokenXDecimals: 9,
          tokenYDecimals: 6,
          currentPrice: parseFloat(p.current_price ?? '0'),
          baseFee: parseFloat(p.base_fee_percentage ?? '0'),
          tvl: parseFloat(p.liquidity ?? '0'),
          volume24h: parseFloat(p.trade_volume_24h ?? '0'),
          apr: parseFloat(p.apr ?? '0'),
          binStep: p.bin_step ?? 0,
          activeBinId: 0,
        };
      });
    } catch (err) {
      console.error('Failed to fetch Meteora pools:', err);
      return [];
    }
  }

  /** Get specific pool info by address.
   *
   * Hits Meteora's new datapi endpoint (`dlmm.datapi.meteora.ag/pools/<addr>`)
   * directly from the browser — CORS is open for this host. The legacy
   * `dlmm-api.meteora.ag/pair/<addr>` URL was deprecated in early 2026 and
   * returns 404, which is why earlier consumers of this method silently
   * got `null` and the action card opened with empty Token A/B fields.
   */
  async getPool(address: string): Promise<MeteoraPool | null> {
    try {
      const resp = await fetch(`https://dlmm.datapi.meteora.ag/pools/${address}`)
        .then(r => r.ok ? r.json() : null);
      if (!resp) return null;
      const tokenX = resp.token_x ?? {};
      const tokenY = resp.token_y ?? {};
      const cfg = resp.pool_config ?? {};
      const vol = resp.volume ?? {};
      const decX = tokenX.decimals ?? 9;
      const decY = tokenY.decimals ?? 6;
      const binStep = cfg.bin_step ?? resp.bin_step ?? 0;
      const currentPrice = parseFloat(resp.current_price ?? '0');
      // datapi /pools omits active_id, so derive it from the inverse of the
      // DLMM bin formula:  humanPrice = (1 + binStep/10000)^activeBinId * 10^(decY-decX)
      // Without this the action card defaults activeBinId to 0 and shows a
      // bogus "1 X = 1 Y" ratio regardless of true pool price.
      const activeBinId = resp.active_id ??
        (binStep > 0 && currentPrice > 0
          ? Math.round(
              Math.log(currentPrice * Math.pow(10, decX - decY)) /
              Math.log(1 + binStep / 10000)
            )
          : 0);
      return {
        address: resp.address,
        tokenXMint: tokenX.address ?? resp.mint_x ?? '',
        tokenYMint: tokenY.address ?? resp.mint_y ?? '',
        tokenXSymbol: tokenX.symbol ?? '',
        tokenYSymbol: tokenY.symbol ?? '',
        tokenXDecimals: decX,
        tokenYDecimals: decY,
        currentPrice,
        baseFee: parseFloat(cfg.base_fee_pct ?? resp.base_fee_percentage ?? '0'),
        tvl: parseFloat(resp.tvl ?? resp.liquidity ?? '0'),
        volume24h: parseFloat(vol['24h'] ?? resp.trade_volume_24h ?? '0'),
        apr: parseFloat(resp.apr ?? '0'),
        binStep,
        activeBinId,
      };
    } catch (err) {
      console.error('Failed to fetch Meteora pool:', err);
      return null;
    }
  }

  /** Get user positions. */
  async getPositions(walletAddress: string): Promise<MeteoraPosition[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any>(
          `${METEORA_API}/position/user/${walletAddress}`
        )
      );
      const raw: any[] = Array.isArray(resp) ? resp : resp?.positions ?? [];
      return raw.map(p => ({
        address: p.address ?? '',
        pool: p.pool_address ?? p.lbPair ?? '',
        owner: walletAddress,
        lowerBinId: p.lower_bin_id ?? 0,
        upperBinId: p.upper_bin_id ?? 0,
        totalTokenXAmount: p.total_x_amount ?? '0',
        totalTokenYAmount: p.total_y_amount ?? '0',
        feeX: p.fee_x ?? '0',
        feeY: p.fee_y ?? '0',
        inRange: p.in_range ?? false,
        liquidityUsd: parseFloat(p.liquidity_usd ?? '0'),
      }));
    } catch (err) {
      console.error('Failed to fetch Meteora positions:', err);
      return [];
    }
  }

  /** Get farms. */
  async getFarms(): Promise<MeteoraFarm[]> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any[]>(`${METEORA_API}/farm/all`)
      );
      return (resp ?? []).map((f: any) => ({
        address: f.address ?? '',
        pool: f.pool ?? f.pair ?? '',
        rewardMint: f.reward_mint ?? '',
        rewardSymbol: f.reward_symbol ?? '',
        apr: parseFloat(f.apr ?? '0'),
        totalStaked: f.total_staked ?? '0',
      }));
    } catch (err) {
      console.error('Failed to fetch Meteora farms:', err);
      return [];
    }
  }

  // ─── Swap Transaction Builders ────────────────────────────────────────────────

  /** Build swap transaction. */
  async buildSwap(
    inputMint: string,
    outputMint: string,
    amount: string,
    slippageBps?: number,
    swapMode?: 'in' | 'out',
    pool?: string
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_swap', {
      inputMint,
      outputMint,
      amount,
      slippageBps,
      swapMode,
      pool,
    });
  }

  // ─── Liquidity Transaction Builders ──────────────────────────────────────────

  /** Build add liquidity transaction. */
  async buildAddLiquidity(
    pool: string,
    amountX: string,
    amountY: string,
    minBinId?: number,
    maxBinId?: number,
    slippageBps?: number
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_add_liquidity', {
      pool,
      amountX,
      amountY,
      minBinId,
      maxBinId,
      slippageBps,
    });
  }

  /** Build remove liquidity transaction. */
  async buildRemoveLiquidity(
    position: string,
    binIds?: number[],
    slippageBps?: number
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_remove_liquidity', {
      position,
      binIds,
      slippageBps,
    });
  }

  /** Build create pool transaction. */
  async buildCreatePool(
    tokenXMint: string,
    tokenYMint: string,
    binStep: number,
    initialPrice: number,
    amountX: string,
    amountY: string,
    baseFee?: number
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_create_pool', {
      tokenXMint,
      tokenYMint,
      binStep,
      initialPrice,
      amountX,
      amountY,
      baseFee,
    });
  }

  // ─── Position Transaction Builders ───────────────────────────────────────────

  /** Build open position transaction. */
  async buildOpenPosition(
    pool: string,
    amountX: string,
    amountY: string,
    minBinId: number,
    maxBinId: number,
    slippageBps?: number
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_open_position', {
      pool,
      amountX,
      amountY,
      minBinId,
      maxBinId,
      slippageBps,
    });
  }

  /** Build close position transaction. */
  async buildClosePosition(
    position: string,
    slippageBps?: number
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_close_position', {
      position,
      slippageBps,
    });
  }

  /** Build add to position transaction. */
  async buildAddToPosition(
    position: string,
    amountX: string,
    amountY: string,
    slippageBps?: number
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_add_to_position', {
      position,
      amountX,
      amountY,
      slippageBps,
    });
  }

  /** Build claim fees transaction. */
  async buildClaimFees(position: string): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_claim_fees', { position });
  }

  /** Build claim rewards transaction. */
  async buildClaimRewards(
    position: string,
    rewardIndex?: number
  ): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_claim_rewards', { position, rewardIndex });
  }

  // ─── Farming Transaction Builders ────────────────────────────────────────────

  /** Build stake transaction. */
  async buildStake(farm: string, amount: string): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_stake', { farm, amount });
  }

  /** Build unstake transaction. */
  async buildUnstake(farm: string, amount: string): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_unstake', { farm, amount });
  }

  /** Build harvest transaction. */
  async buildHarvest(farm: string): Promise<MeteoraBuildResponse | null> {
    return this.buildAction('meteora_harvest', { farm });
  }

  // ─── DLMM data queries (gateway → solana-service → datapi.meteora.ag) ───
  //
  // The legacy endpoints above hit dlmm-api.meteora.ag directly, which returns
  // 404 since Meteora migrated to dlmm.datapi.meteora.ag. The Rust solana
  // service now proxies the new datapi, so for the chat mini-app we fetch
  // through `/solana/actions/build` with type=meteora_dlmm_get_pairs.

  /**
   * Fetch a paginated DLMM pair list. Returns server-side page metadata so the
   * QueryCard can show real "page X / Y of Z" controls.
   *
   * `sortBy` MUST be `<field>:<asc|desc>` — e.g. "tvl:desc", "volume:desc",
   * "fee_tvl_ratio:desc". Bare field names are rejected by the upstream API.
   */
  async fetchDlmmPairs(opts: {
    query?: string;
    page?: number;        // 1-based
    pageSize?: number;
    sortBy?: string;
    filterBy?: string;
  } = {}): Promise<DlmmPairsPage | null> {
    const params: Record<string, unknown> = {};
    if (opts.query)    params['query']    = opts.query;
    if (opts.page)     params['page']     = opts.page;
    if (opts.pageSize) params['pageSize'] = opts.pageSize;
    if (opts.sortBy)   params['sortBy']   = opts.sortBy;
    if (opts.filterBy) params['filterBy'] = opts.filterBy;

    try {
      // Gateway exposes the proxy at /actions/build (NOT /solana/actions/build —
      // that path is unrouted and 404s through the proxy). The auth interceptor
      // adds X-Requested-With + Bearer token automatically.
      const resp = await firstValueFrom(
        this.http.post<{ data?: DlmmPairsPage }>(
          `${environment.apiBase}/actions/build`,
          { type: 'meteora_dlmm_get_pairs', params },
          { withCredentials: true }
        )
      );
      return resp?.data ?? null;
    } catch (err) {
      console.error('Failed to fetch DLMM pairs:', err);
      return null;
    }
  }

  /**
   * Fetch a paginated DAMM v2 pool list. Mirrors `fetchDlmmPairs` — same
   * gateway proxy pattern, same shape of `<page metadata + data[]>`.
   */
  async fetchDammV2Pools(opts: {
    query?: string;
    page?: number;
    pageSize?: number;
    sortBy?: string;
    filterBy?: string;
  } = {}): Promise<DammV2PoolsPage | null> {
    const params: Record<string, unknown> = {};
    if (opts.query)    params['query']     = opts.query;
    if (opts.page)     params['page']      = opts.page;
    if (opts.pageSize) params['page_size'] = opts.pageSize;
    if (opts.sortBy)   params['sort_by']   = opts.sortBy;
    if (opts.filterBy) params['filter_by'] = opts.filterBy;
    try {
      const resp = await firstValueFrom(
        this.http.post<{ data?: DammV2PoolsPage }>(
          `${environment.apiBase}/actions/build`,
          { type: 'meteora_dammv2_get_pools', params },
          { withCredentials: true }
        )
      );
      return resp?.data ?? null;
    } catch (err) {
      console.error('Failed to fetch DAMM v2 pools:', err);
      return null;
    }
  }

  /**
   * Fetch the legacy DAMM v1 pool list. The upstream API is a flat array
   * (no server-side pagination metadata), so we slice client-side.
   */
  async fetchDammV1Pools(opts: {
    query?: string;
    limit?: number;
    offset?: number;
    sortBy?: string;
    isLst?: boolean;
    isForex?: boolean;
  } = {}): Promise<DammV1Pool[] | null> {
    const params: Record<string, unknown> = {};
    if (opts.query !== undefined)     params['query']     = opts.query;
    if (opts.limit !== undefined)     params['limit']     = opts.limit;
    if (opts.offset !== undefined)    params['offset']    = opts.offset;
    if (opts.sortBy)                   params['sortBy']    = opts.sortBy;
    if (opts.isLst !== undefined)     params['isLst']     = opts.isLst;
    if (opts.isForex !== undefined)   params['isForex']   = opts.isForex;
    try {
      const resp = await firstValueFrom(
        this.http.post<{ data?: DammV1Pool[] }>(
          `${environment.apiBase}/actions/build`,
          { type: 'meteora_dammv1_get_pools', params },
          { withCredentials: true }
        )
      );
      return resp?.data ?? null;
    } catch (err) {
      console.error('Failed to fetch DAMM v1 pools:', err);
      return null;
    }
  }

  // ─── Private Helpers ────────────────────────────────────────────────────────

  private async buildAction(
    type: string,
    params: Record<string, unknown>
  ): Promise<MeteoraBuildResponse | null> {
    try {
      const resp = await firstValueFrom(
        this.http.post<MeteoraBuildResponse>(
          `${environment.apiBase}/solana/actions/build`,
          { type, params }
        )
      );
      return resp;
    } catch (err) {
      console.error(`Meteora build ${type} error:`, err);
      return null;
    }
  }
}

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

  /** Get specific pool info by address. */
  async getPool(address: string): Promise<MeteoraPool | null> {
    try {
      const resp = await firstValueFrom(
        this.http.get<any>(`${METEORA_API}/pair/${address}`)
      );
      if (!resp) return null;
      const [symX, symY] = (resp.name ?? '-').split('-');
      return {
        address: resp.address,
        tokenXMint: resp.mint_x ?? '',
        tokenYMint: resp.mint_y ?? '',
        tokenXSymbol: symX ?? '',
        tokenYSymbol: symY ?? '',
        tokenXDecimals: 9,
        tokenYDecimals: 6,
        currentPrice: parseFloat(resp.current_price ?? '0'),
        baseFee: parseFloat(resp.base_fee_percentage ?? '0'),
        tvl: parseFloat(resp.liquidity ?? '0'),
        volume24h: parseFloat(resp.trade_volume_24h ?? '0'),
        apr: parseFloat(resp.apr ?? '0'),
        binStep: resp.bin_step ?? 0,
        activeBinId: 0,
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

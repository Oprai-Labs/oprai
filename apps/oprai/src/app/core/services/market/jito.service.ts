import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../api.service';

// ─────────────────────────────────────────────────────────────────────────────
// Constants — verified from official Jito documentation
// ─────────────────────────────────────────────────────────────────────────────

/** JitoSOL token mint (mainnet). Source: Jito staking integration docs. */
export const JITOSOL_MINT = 'J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn';

/** Jito stake pool address (mainnet). */
export const JITO_STAKE_POOL = 'Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb';

/** Kobe analytics API base URL. All analytics endpoints live here. */
export const JITO_KOBE_API = 'https://kobe.mainnet.jito.network';

/**
 * Jito tip accounts — rotate randomly to reduce contention.
 * Source: Jito block engine documentation.
 */
export const JITO_TIP_ACCOUNTS = [
  '96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5',
  'HFqU5x63VTqvQss8hp11i4bVmKafdMkF7nQ6kY91v3oS',
  'Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY',
  'ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49',
  'DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh',
  'ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt',
  'DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL',
  '3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT',
];

// ─────────────────────────────────────────────────────────────────────────────
// Types — Kobe API responses
// ─────────────────────────────────────────────────────────────────────────────

export interface TimeSeriesPoint {
  data: number;
  date: string;
}

/** Response from /api/v1/stake_pool_stats */
export interface StakePoolStats {
  aggregated_mev_rewards?: number;
  mev_rewards?: TimeSeriesPoint[];
  tvl?: TimeSeriesPoint[];
  apy?: TimeSeriesPoint[];
  num_validators?: TimeSeriesPoint[];
  supply?: TimeSeriesPoint[];
}

/** Response from /api/v1/jitosol_sol_ratio */
export interface JitosolSolRatio {
  ratios: TimeSeriesPoint[];
}

/** Entry from /api/v1/preferred_withdraw_validator_list */
export interface PreferredWithdrawValidator {
  rank: number;
  vote_account: string;
  withdrawable_lamports: number;
  stake_account: string;
}

/** Entry from /api/v1/staker_rewards */
export interface StakerRewardEntry {
  claimant: string;
  stake_authority: string;
  withdraw_authority: string;
  validator_vote_account: string;
  claim_status_account: string;
  priority_fee_claim_status_account: string | null;
  epoch: number;
  amount: number;
  priority_fee_amount: number | null;
}

export interface StakerRewardsResponse {
  rewards: StakerRewardEntry[];
  total_count: number;
}

/** Entry from /api/v1/validator_rewards */
export interface ValidatorRewardEntry {
  vote_account: string;
  mev_revenue: number;
  mev_commission: number;
  priority_fee_revenue: number;
  priority_fee_commission: number | null;
  num_stakers: number;
  epoch: number;
  claim_status_account: string;
}

export interface ValidatorRewardsResponse {
  rewards: ValidatorRewardEntry[];
  total_count: number;
}

/** Entry from /api/v1/validators and /api/v1/jitosol_validators */
export interface ValidatorEntry {
  vote_account: string;
  mev_commission_bps: number;
  mev_rewards: number;
  priority_fee_commission_bps: number | null;
  priority_fee_rewards: number | null;
  running_jito: boolean;
  running_bam: boolean;
  active_stake: number;
  jito_sol_active_lamports?: number;
  jito_directed_stake_target?: boolean | null;
  jito_directed_stake_lamports?: number | null;
}

export interface ValidatorsResponse {
  validators: ValidatorEntry[];
}

/** Entry from /api/v1/validators/{vote_account} */
export interface ValidatorHistoryEntry {
  epoch: number;
  mev_commission_bps: number;
  mev_rewards: number;
  priority_fee_commission_bps: number | null;
  priority_fee_rewards: number | null;
}

/** Response from /api/v1/mev_rewards */
export interface MevRewardsResponse {
  epoch: number;
  total_network_mev_lamports: number;
  jito_stake_weight_lamports: number;
  mev_reward_per_lamport: number;
}

/** Entry from /api/v1/daily_mev_rewards */
export interface DailyMevRewardEntry {
  day: string;
  count_mev_tips: number;
  jito_tips: number;
  tippers: number;
  validator_tips: number;
}

/** Response from /api/v1/jito_stake_over_time */
export interface JitoStakeOverTime {
  stake_ratio_over_time: Record<string, number>;
}

/** Response from /api/v1/steward_events */
export interface StewardEventsResponse {
  events: StewardEventEntry[];
}

export interface StewardEventEntry {
  signature: string;
  event_type: string;
  vote_account?: string;
  timestamp?: string;
  epoch?: number;
  slot?: number;
  data?: Record<string, unknown>;
  tx_error?: string | null;
}

/** Response from /api/v1/bam_epoch_metrics */
export interface BamEpochMetrics {
  allocation_bps: number;
  available_bam_delegation_stake: number;
  bam_stake: number;
  eligible_bam_validator_count: number;
  epoch: number;
  jitosol_stake: number;
  timestamp: number;
  total_stake: number;
}

export interface BamEpochMetricsResponse {
  bam_epoch_metrics: BamEpochMetrics;
}

export interface BamValidatorEntry {
  active_stake: number;
  epoch: number;
  identity_account: string;
  is_eligible: boolean;
  ineligibility_reason: string | null;
  timestamp: number;
  vote_account: string;
}

export interface BamValidatorsResponse {
  bam_validators: BamValidatorEntry[];
}

export interface BamBlacklistEntry {
  vote_account: string;
  added_epoch: number;
}

export interface BamValidatorScore {
  vote_account: string;
  score: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Types — Tip floor & Bundle
// ─────────────────────────────────────────────────────────────────────────────

export interface JitoTipFloor {
  landedTips25thPercentile: number;
  landedTips50thPercentile: number;
  landedTips75thPercentile: number;
  landedTips95thPercentile: number;
}

export interface JitoBundleStatus {
  bundleId: string;
  status: 'pending' | 'processing' | 'landed' | 'failed' | 'rejected';
  landedSlot?: number;
  transactions: string[];
  error?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Types — Backend build responses
// ─────────────────────────────────────────────────────────────────────────────

export interface JitoBuildResponse {
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
  executionSteps?: {
    type: 'jito_bundle';
    transactions: string[];
    tipAmount: number;
    submitUrl: string;
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Service
// ─────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class JitoService {
  private readonly api = inject(ApiService);

  // ── Stake Pool Stats (kobe API) ────────────────────────────────────────────

  /**
   * Fetch stake pool stats: TVL, APY, supply, validator count.
   * Source: kobe.mainnet.jito.network/api/v1/stake_pool_stats
   * Frontend APY calculation: use the last entry of `apy` array × 100.
   */
  async getStakePoolStats(start?: string, end?: string): Promise<StakePoolStats | null> {
    try {
      const body: Record<string, unknown> = {
        bucket_type: 'Daily',
        sort_by: { field: 'BlockTime', order: 'Asc' },
      };
      if (start && end) {
        body['range_filter'] = { start, end };
      }
      return await firstValueFrom(
        this.api.post<StakePoolStats>('/market/jito/stake-pool-stats', body)
      );
    } catch {
      return null;
    }
  }

  /** Current APY as percentage (e.g. 7.18). Uses most recent data point per Jito docs. */
  async getCurrentApy(): Promise<number | null> {
    const stats = await this.getStakePoolStats();
    const last = stats?.apy?.at(-1);
    return last ? last.data * 100 : null;
  }

  // ── JitoSOL/SOL Ratio (kobe API) ──────────────────────────────────────────

  /**
   * Fetch jitoSOL/SOL exchange rate history.
   * Source: kobe.mainnet.jito.network/api/v1/jitosol_sol_ratio
   */
  async getJitosolSolRatio(start?: string, end?: string): Promise<JitosolSolRatio | null> {
    try {
      const body: Record<string, unknown> = {};
      if (start && end) body['range_filter'] = { start, end };
      return await firstValueFrom(
        this.api.post<JitosolSolRatio>('/market/jito/jitosol-sol-ratio', body)
      );
    } catch {
      return null;
    }
  }

  /**
   * Latest jitoSOL/SOL exchange rate from Jito's Kobe analytics API.
   * Returns null if the live data is unavailable — callers must handle that
   * (no static constant fallback). The Rust solana-service has an additional
   * on-chain fallback for transaction-build paths; UI previews simply hide
   * when null.
   */
  async getExchangeRate(): Promise<number | null> {
    const ratio = await this.getJitosolSolRatio();
    const latest = ratio?.ratios.at(-1)?.data;
    return typeof latest === 'number' && Number.isFinite(latest) && latest > 0 ? latest : null;
  }

  // ── MEV & Claims API (kobe) ────────────────────────────────────────────────

  /**
   * Individual claimable MEV rewards from tip distribution merkle trees.
   * Source: kobe.mainnet.jito.network/api/v1/staker_rewards
   */
  async getStakerRewards(params: {
    stakeAuthority?: string;
    validatorVoteAccount?: string;
    epoch?: number;
    page?: number;
    limit?: number;
    sortOrder?: 'asc' | 'desc';
  } = {}): Promise<StakerRewardsResponse | null> {
    try {
      const body: Record<string, unknown> = {};
      if (params.stakeAuthority) body['stake_authority'] = params.stakeAuthority;
      if (params.validatorVoteAccount) body['validator_vote_account'] = params.validatorVoteAccount;
      if (params.epoch !== undefined) body['epoch'] = params.epoch;
      if (params.page !== undefined) body['page'] = params.page;
      if (params.limit !== undefined) body['limit'] = params.limit;
      if (params.sortOrder) body['sort_order'] = params.sortOrder;
      return await firstValueFrom(
        this.api.post<StakerRewardsResponse>('/market/jito/staker-rewards', body)
      );
    } catch {
      return null;
    }
  }

  /**
   * Aggregated MEV rewards per validator.
   * Source: kobe.mainnet.jito.network/api/v1/validator_rewards
   */
  async getValidatorRewards(params: {
    voteAccount?: string;
    epoch?: number;
    page?: number;
    limit?: number;
    sortOrder?: 'asc' | 'desc';
  } = {}): Promise<ValidatorRewardsResponse | null> {
    try {
      const body: Record<string, unknown> = {};
      if (params.voteAccount) body['vote_account'] = params.voteAccount;
      if (params.epoch !== undefined) body['epoch'] = params.epoch;
      if (params.page !== undefined) body['page'] = params.page;
      if (params.limit !== undefined) body['limit'] = params.limit;
      if (params.sortOrder) body['sort_order'] = params.sortOrder;
      return await firstValueFrom(
        this.api.post<ValidatorRewardsResponse>('/market/jito/validator-rewards', body)
      );
    } catch {
      return null;
    }
  }

  // ── Validator API (kobe) ───────────────────────────────────────────────────

  /** All Solana validators with MEV metrics for a given epoch. */
  async getValidators(epoch?: number): Promise<ValidatorsResponse | null> {
    try {
      const body = epoch !== undefined ? { epoch } : {};
      return await firstValueFrom(
        this.api.post<ValidatorsResponse>('/market/jito/validators', body)
      );
    } catch {
      return null;
    }
  }

  /** Validators in the JitoSOL stake pool for a given epoch. */
  async getJitosolValidators(epoch?: number): Promise<ValidatorsResponse | null> {
    try {
      const body = epoch !== undefined ? { epoch } : {};
      return await firstValueFrom(
        this.api.post<ValidatorsResponse>('/market/jito/jitosol-validators', body)
      );
    } catch {
      return null;
    }
  }

  /** Historical reward data for a single validator, sorted by epoch desc. */
  async getValidatorHistory(voteAccount: string): Promise<ValidatorHistoryEntry[]> {
    try {
      return await firstValueFrom(
        this.api.get<ValidatorHistoryEntry[]>(`/market/jito/validators/${voteAccount}`)
      );
    } catch {
      return [];
    }
  }

  /** Network MEV stats for a given epoch. */
  async getMevRewards(epoch?: number): Promise<MevRewardsResponse | null> {
    try {
      const body = epoch !== undefined ? { epoch } : {};
      return await firstValueFrom(
        this.api.post<MevRewardsResponse>('/market/jito/mev-rewards', body)
      );
    } catch {
      return null;
    }
  }

  /** MEV tips aggregated by calendar day. */
  async getDailyMevRewards(): Promise<DailyMevRewardEntry[]> {
    try {
      return await firstValueFrom(
        this.api.get<DailyMevRewardEntry[]>('/market/jito/daily-mev-rewards')
      );
    } catch {
      return [];
    }
  }

  /** Fraction of Solana stake on Jito validators, per epoch. */
  async getJitoStakeOverTime(): Promise<JitoStakeOverTime | null> {
    try {
      return await firstValueFrom(
        this.api.get<JitoStakeOverTime>('/market/jito/stake-over-time')
      );
    } catch {
      return null;
    }
  }

  /**
   * Preferred validators for withdrawal — minimises rebalancing.
   * Use `randomized: true` for multisig/durable-nonce scenarios.
   */
  async getPreferredWithdrawValidators(
    limit = 10,
    minStakeThresholdSol?: number,
    randomized = false
  ): Promise<PreferredWithdrawValidator[]> {
    try {
      let url = `/market/jito/preferred-withdraw-validators?limit=${limit}`;
      if (minStakeThresholdSol !== undefined) url += `&min_stake_threshold=${minStakeThresholdSol}`;
      if (randomized) url += `&randomized=true`;
      return await firstValueFrom(
        this.api.get<PreferredWithdrawValidator[]>(url)
      );
    } catch {
      return [];
    }
  }

  /** Stake-weighted average MEV commission rates by epoch. */
  async getMevCommissionAverageOverTime(): Promise<unknown | null> {
    try {
      return await firstValueFrom(
        this.api.get('/market/jito/mev-commission-avg')
      );
    } catch {
      return null;
    }
  }

  // ── Steward API (kobe) ─────────────────────────────────────────────────────

  /**
   * Jito Steward program events with filtering and pagination.
   * Event types: ScoreComponents, InstantUnstakeComponents, RebalanceEvent,
   *   DecreaseComponents, StateTransition, AutoAddValidatorEvent,
   *   AutoRemoveValidatorEvent, EpochMaintenanceEvent
   */
  async getStewardEvents(params: {
    eventType?: string;
    voteAccount?: string;
    epoch?: number;
    limit?: number;
    skip?: number;
  } = {}): Promise<StewardEventsResponse | null> {
    try {
      const body: Record<string, unknown> = {};
      if (params.eventType) body['event_type'] = params.eventType;
      if (params.voteAccount) body['vote_account'] = params.voteAccount;
      if (params.epoch !== undefined) body['epoch'] = params.epoch;
      if (params.limit !== undefined) body['limit'] = params.limit;
      if (params.skip !== undefined) body['skip'] = params.skip;
      return await firstValueFrom(
        this.api.post<StewardEventsResponse>('/market/jito/steward-events', body)
      );
    } catch {
      return null;
    }
  }

  // ── BAM API (kobe) ─────────────────────────────────────────────────────────

  /** BAM delegation metrics for a given epoch. */
  async getBamEpochMetrics(epoch: number): Promise<BamEpochMetricsResponse | null> {
    try {
      return await firstValueFrom(
        this.api.get<BamEpochMetricsResponse>(`/market/jito/bam-epoch-metrics?epoch=${epoch}`)
      );
    } catch {
      return null;
    }
  }

  /** BAM validators for a given epoch. */
  async getBamValidators(epoch: number): Promise<BamValidatorsResponse | null> {
    try {
      return await firstValueFrom(
        this.api.get<BamValidatorsResponse>(`/market/jito/bam-validators?epoch=${epoch}`)
      );
    } catch {
      return null;
    }
  }

  /** Vote accounts blacklisted from BAM delegation. */
  async getBamDelegationBlacklist(): Promise<BamBlacklistEntry[]> {
    try {
      return await firstValueFrom(
        this.api.get<BamBlacklistEntry[]>('/market/jito/bam-delegation-blacklist')
      );
    } catch {
      return [];
    }
  }

  /** Scoring components for a specific validator. */
  async getBamValidatorScore(epoch: number, voteAccount: string): Promise<BamValidatorScore | null> {
    try {
      return await firstValueFrom(
        this.api.get<BamValidatorScore>(
          `/market/jito/bam-validator-score?epoch=${epoch}&vote_account=${voteAccount}`
        )
      );
    } catch {
      return null;
    }
  }

  // ── Tip Floor (Block Engine) ───────────────────────────────────────────────

  /** Current Jito tip floor percentiles. Routed through gateway to avoid CORS. */
  async getTipFloor(): Promise<JitoTipFloor | null> {
    try {
      const resp = await firstValueFrom(
        this.api.get<any[]>('/market/jito/tip-floor')
      );
      const r = Array.isArray(resp) ? resp[0] : resp;
      if (!r) return null;
      return {
        landedTips25thPercentile: r.landed_tips_25th_percentile ?? 0,
        landedTips50thPercentile: r.landed_tips_50th_percentile ?? 0,
        landedTips75thPercentile: r.landed_tips_75th_percentile ?? 0,
        landedTips95thPercentile: r.landed_tips_95th_percentile ?? 0,
      };
    } catch {
      return null;
    }
  }

  /** Suggested tip amount in SOL for a given priority level. */
  async getSuggestedTip(priority: 'low' | 'medium' | 'high' = 'medium'): Promise<number> {
    const floor = await this.getTipFloor();
    if (!floor) return 0.001;
    switch (priority) {
      case 'low': return floor.landedTips25thPercentile;
      case 'high': return floor.landedTips75thPercentile;
      default: return floor.landedTips50thPercentile;
    }
  }

  /** Random tip account from the rotation list. */
  getRandomTipAccount(): string {
    return JITO_TIP_ACCOUNTS[Math.floor(Math.random() * JITO_TIP_ACCOUNTS.length)];
  }

  // ── Bundle Status (Block Engine, via gateway) ──────────────────────────────

  async getBundleStatus(bundleId: string): Promise<JitoBundleStatus | null> {
    try {
      return await firstValueFrom(
        this.api.get<JitoBundleStatus>(`/market/jito/bundle/${bundleId}`)
      );
    } catch {
      return null;
    }
  }

  // ── Stake / Unstake / Tip / Bundle — via backend ───────────────────────────

  /** Build a jitoSOL stake transaction (SOL → jitoSOL). */
  async buildStake(amount: string, slippageBps?: number): Promise<JitoBuildResponse | null> {
    return this.buildAction('jito_stake', { amount, slippageBps: slippageBps ?? 50 });
  }

  /**
   * Build a jitoSOL unstake transaction.
   *
   * @param instant - false (default): `withdraw_stake` — creates a stake account,
   *   requires ~1-epoch deactivation before SOL is liquid. Mirrors `useReserve: false`.
   *   true: `withdraw_sol` — instant liquid SOL from the pool reserve.
   *   Subject to reserve liquidity. Mirrors `useReserve: true`.
   */
  async buildUnstake(
    amount: string,
    instant = false,
    slippageBps?: number
  ): Promise<JitoBuildResponse | null> {
    return this.buildAction('jito_unstake', { amount, instant, slippageBps: slippageBps ?? 50 });
  }

  /** Build a Jito MEV tip transaction. */
  async buildTip(amount: string, transaction?: string): Promise<JitoBuildResponse | null> {
    return this.buildAction('jito_tip', { amount, transaction });
  }

  /** Build a Jito MEV bundle (1–5 transactions, atomic). */
  async buildBundle(transactions: string[], tipAmount?: string): Promise<JitoBuildResponse | null> {
    return this.buildAction('jito_bundle', { transactions, tipAmount });
  }

  /** Submit a bundle via the gateway (avoids CORS with Jito Block Engine). */
  async submitBundle(
    transactionsBase58: string[],
    tipAmount: number
  ): Promise<{ bundleId: string } | null> {
    try {
      return await firstValueFrom(
        this.api.post<{ bundleId: string }>('/actions/build', {
          type: 'jito_bundle',
          params: { transactions: transactionsBase58, tipAmount: String(tipAmount) },
        })
      );
    } catch {
      return null;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Private helpers
  // ─────────────────────────────────────────────────────────────────────────

  private async buildAction(
    type: string,
    params: Record<string, unknown>
  ): Promise<JitoBuildResponse | null> {
    try {
      return await firstValueFrom(
        this.api.post<JitoBuildResponse>('/actions/build', { type, params })
      );
    } catch (err) {
      console.error(`Jito ${type} build error:`, err);
      return null;
    }
  }
}

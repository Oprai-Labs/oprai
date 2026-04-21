/**
 * RollbackService
 *
 * Provides undo functionality for reversible actions.
 * Calculates and executes reverse actions based on original action type and result.
 *
 * Reversible actions:
 *   swap ↔ swap (swap back)
 *   stake ↔ unstake
 *   lend ↔ withdraw
 *   borrow ↔ repay
 *   add_liquidity ↔ remove_liquidity
 *   limit_order → cancel_limit_order
 *   dca → cancel_dca
 *   perp_open → perp_close
 */

import { Injectable, inject } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { SolanaActionService, ActionCallbacks } from '@features/chat/services/solana-action.service';

// ── Types ─────────────────────────────────────────────────────────────────────

export type RollbackStatus = 'pending' | 'in_progress' | 'completed' | 'failed' | 'not_reversible';

export interface ActionSnapshot {
  id: string;
  actionType: string;
  params: Record<string, unknown>;
  timestamp: number;
  signature?: string;
  walletAddress?: string;
  isReversible: boolean;
  reversalAction?: string;
  reversalParams?: Record<string, unknown>;
  /** Result from original execution (used to calculate reversal) */
  executionResult?: {
    outAmount?: string;
    inAmount?: string;
    receivedAmount?: string;
    lpAmountReceived?: string;
    depositReceiptAmount?: string;
    withdrawnAmount?: string;
    borrowedAmount?: string;
    filledSize?: string;
    orderKey?: string;
  };
}

export interface RollbackResult {
  success: boolean;
  snapshotId: string;
  status: RollbackStatus;
  rollbackSignature?: string;
  error?: string;
  timestamp: number;
}

export interface RollbackStats {
  totalSnapshots: number;
  reversibleSnapshots: number;
  byAction: Record<string, number>;
}

// ── Reversibility Map ─────────────────────────────────────────────────────────

interface ReversibilityInfo {
  isReversible: boolean;
  reversalAction: string | null;
  /** Maps original result fields to reversal params */
  paramMapping: Record<string, string>;
}

const REVERSIBLE_ACTIONS: Record<string, ReversibilityInfo> = {
  swap: {
    isReversible: true,
    reversalAction: 'swap',
    paramMapping: {
      inputMint: 'outputMint',
      outputMint: 'inputMint',
      amount: 'outAmount',
    },
  },
  transfer: {
    isReversible: false, // Can't reverse a transfer (need recipient to send back)
    reversalAction: null,
    paramMapping: {},
  },
  stake: {
    isReversible: true,
    reversalAction: 'unstake',
    paramMapping: { amount: 'receivedAmount' },
  },
  unstake: {
    isReversible: true,
    reversalAction: 'stake',
    paramMapping: { amount: 'receivedAmount' },
  },
  jupsol_stake: {
    isReversible: true,
    reversalAction: 'jupsol_unstake',
    paramMapping: { amount: 'outAmount' },
  },
  jupsol_unstake: {
    isReversible: true,
    reversalAction: 'jupsol_stake',
    paramMapping: { amount: 'outAmount' },
  },
  add_liquidity: {
    isReversible: true,
    reversalAction: 'remove_liquidity',
    paramMapping: { lpAmount: 'lpAmountReceived' },
  },
  remove_liquidity: {
    isReversible: true,
    reversalAction: 'add_liquidity',
    paramMapping: {},
  },
  lend: {
    isReversible: true,
    reversalAction: 'withdraw_lend',
    paramMapping: { amount: 'depositReceiptAmount' },
  },
  withdraw_lend: {
    isReversible: true,
    reversalAction: 'lend',
    paramMapping: { amount: 'withdrawnAmount' },
  },
  borrow: {
    isReversible: true,
    reversalAction: 'repay',
    paramMapping: { amount: 'borrowedAmount' },
  },
  repay: {
    isReversible: false,
    reversalAction: null,
    paramMapping: {},
  },
  limit_order: {
    isReversible: true,
    reversalAction: 'cancel_limit_order',
    paramMapping: { orderKey: 'orderKey' },
  },
  cancel_limit_order: {
    isReversible: false,
    reversalAction: null,
    paramMapping: {},
  },
  dca: {
    isReversible: true,
    reversalAction: 'cancel_dca',
    paramMapping: { order: 'orderKey' },
  },
  cancel_dca: {
    isReversible: false,
    reversalAction: null,
    paramMapping: {},
  },
  perp_open: {
    isReversible: true,
    reversalAction: 'perp_close',
    paramMapping: { size: 'filledSize' },
  },
  perp_close: {
    isReversible: false,
    reversalAction: null,
    paramMapping: {},
  },
  close_position: {
    isReversible: false,
    reversalAction: null,
    paramMapping: {},
  },
  bridge: {
    isReversible: false, // Cross-chain bridges can't be easily reversed
    reversalAction: null,
    paramMapping: {},
  },
};

const TTL_HOURS = 24;
const STORAGE_KEY = 'oprai-action-snapshots';

// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class RollbackService {
  private readonly actionService = inject(SolanaActionService);

  /** All action snapshots — components can subscribe to show undo history */
  readonly snapshots$ = new BehaviorSubject<Map<string, ActionSnapshot>>(new Map());

  /** Currently executing rollbacks */
  readonly pendingRollbacks$ = new BehaviorSubject<Set<string>>(new Set());

  constructor() {
    this.loadFromStorage();
    this.cleanupExpired();
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Check if an action type is reversible.
   */
  isActionReversible(actionType: string): boolean {
    return REVERSIBLE_ACTIONS[actionType]?.isReversible ?? false;
  }

  /**
   * Get all reversible action types.
   */
  getReversibleActions(): string[] {
    return Object.entries(REVERSIBLE_ACTIONS)
      .filter(([_, info]) => info.isReversible)
      .map(([action]) => action);
  }

  /**
   * Create a snapshot before executing an action.
   * Call this BEFORE execution, then call saveSnapshotResult AFTER successful execution.
   */
  createSnapshot(
    actionType: string,
    params: Record<string, unknown>,
    walletAddress?: string
  ): ActionSnapshot {
    const id = this.generateId();
    const reversibility = REVERSIBLE_ACTIONS[actionType] ?? {
      isReversible: false,
      reversalAction: null,
      paramMapping: {},
    };

    const snapshot: ActionSnapshot = {
      id,
      actionType,
      params,
      timestamp: Date.now(),
      walletAddress,
      isReversible: reversibility.isReversible,
      reversalAction: reversibility.reversalAction ?? undefined,
    };

    // Store in memory (not persisted until saveSnapshotResult is called)
    const current = new Map(this.snapshots$.value);
    current.set(id, snapshot);
    this.snapshots$.next(current);

    return snapshot;
  }

  /**
   * Update snapshot with execution result and persist.
   * Call this AFTER successful action execution.
   */
  saveSnapshotResult(
    snapshotId: string,
    signature: string,
    executionResult: ActionSnapshot['executionResult']
  ): void {
    const current = new Map(this.snapshots$.value);
    const snapshot = current.get(snapshotId);

    if (!snapshot) return;

    snapshot.signature = signature;
    snapshot.executionResult = executionResult;

    // Calculate reversal params based on execution result
    if (snapshot.isReversible && snapshot.reversalAction && executionResult) {
      snapshot.reversalParams = this.calculateReversalParams(
        snapshot.actionType,
        snapshot.params,
        executionResult
      );
    }

    current.set(snapshotId, snapshot);
    this.snapshots$.next(current);
    this.saveToStorage();
  }

  /**
   * Execute undo for a snapshot.
   */
  async undo(snapshotId: string, callbacks?: ActionCallbacks): Promise<RollbackResult> {
    const snapshot = this.snapshots$.value.get(snapshotId);

    if (!snapshot) {
      return {
        success: false,
        snapshotId,
        status: 'failed',
        error: 'Snapshot not found',
        timestamp: Date.now(),
      };
    }

    if (!snapshot.isReversible) {
      return {
        success: false,
        snapshotId,
        status: 'not_reversible',
        error: `Action '${snapshot.actionType}' is not reversible`,
        timestamp: Date.now(),
      };
    }

    if (!snapshot.reversalAction || !snapshot.reversalParams) {
      return {
        success: false,
        snapshotId,
        status: 'not_reversible',
        error: 'No reversal action defined',
        timestamp: Date.now(),
      };
    }

    // Mark as in progress
    const pending = new Set(this.pendingRollbacks$.value);
    pending.add(snapshotId);
    this.pendingRollbacks$.next(pending);

    try {
      const signature = await this.actionService.execute(
        {
          type: snapshot.reversalAction,
          params: snapshot.reversalParams as Record<string, string>,
          raw: '',
        },
        {
          onQuote: callbacks?.onQuote,
          onSign: callbacks?.onSign,
          onSubmit: callbacks?.onSubmit,
          onConfirm: (confirmResult?: string) => {
            // Remove snapshot after successful rollback
            this.removeSnapshot(snapshotId);
            callbacks?.onConfirm?.(confirmResult);
          },
        }
      );

      pending.delete(snapshotId);
      this.pendingRollbacks$.next(pending);

      if (signature) {
        return {
          success: true,
          snapshotId,
          status: 'completed',
          rollbackSignature: signature,
          timestamp: Date.now(),
        };
      } else {
        return {
          success: false,
          snapshotId,
          status: 'failed',
          error: 'Transaction failed',
          timestamp: Date.now(),
        };
      }
    } catch (err) {
      pending.delete(snapshotId);
      this.pendingRollbacks$.next(pending);

      return {
        success: false,
        snapshotId,
        status: 'failed',
        error: err instanceof Error ? err.message : String(err),
        timestamp: Date.now(),
      };
    }
  }

  /**
   * Undo the most recent reversible action.
   */
  async undoLast(walletAddress?: string, actionType?: string): Promise<RollbackResult | null> {
    const snapshots = this.listSnapshots({
      walletAddress,
      actionType,
      reversibleOnly: true,
      limit: 1,
    });

    if (snapshots.length === 0) {
      return null;
    }

    return this.undo(snapshots[0].id);
  }

  /**
   * List snapshots with optional filters.
   */
  listSnapshots(options: {
    walletAddress?: string;
    actionType?: string;
    reversibleOnly?: boolean;
    limit?: number;
  } = {}): ActionSnapshot[] {
    let snapshots = Array.from(this.snapshots$.value.values());

    if (options.walletAddress) {
      snapshots = snapshots.filter(s => s.walletAddress === options.walletAddress);
    }
    if (options.actionType) {
      snapshots = snapshots.filter(s => s.actionType === options.actionType);
    }
    if (options.reversibleOnly) {
      snapshots = snapshots.filter(s => s.isReversible);
    }

    // Sort by timestamp, most recent first
    snapshots.sort((a, b) => b.timestamp - a.timestamp);

    return snapshots.slice(0, options.limit ?? 50);
  }

  /**
   * Get a single snapshot by ID.
   */
  getSnapshot(snapshotId: string): ActionSnapshot | undefined {
    return this.snapshots$.value.get(snapshotId);
  }

  /**
   * Get statistics about stored snapshots.
   */
  getStats(): RollbackStats {
    const snapshots = Array.from(this.snapshots$.value.values());
    const byAction: Record<string, number> = {};

    for (const s of snapshots) {
      byAction[s.actionType] = (byAction[s.actionType] ?? 0) + 1;
    }

    return {
      totalSnapshots: snapshots.length,
      reversibleSnapshots: snapshots.filter(s => s.isReversible).length,
      byAction,
    };
  }

  /**
   * Remove a snapshot (after successful rollback or manual cleanup).
   */
  removeSnapshot(snapshotId: string): void {
    const current = new Map(this.snapshots$.value);
    current.delete(snapshotId);
    this.snapshots$.next(current);
    this.saveToStorage();
  }

  /**
   * Clear all snapshots.
   */
  clearAll(): void {
    this.snapshots$.next(new Map());
    this.saveToStorage();
  }

  // ── Private Helpers ────────────────────────────────────────────────────────

  private calculateReversalParams(
    actionType: string,
    originalParams: Record<string, unknown>,
    executionResult: ActionSnapshot['executionResult']
  ): Record<string, unknown> {
    const reversibility = REVERSIBLE_ACTIONS[actionType];
    if (!reversibility) return {};

    const reversalParams: Record<string, unknown> = {};

    // Copy relevant original params
    for (const [key, value] of Object.entries(originalParams)) {
      // Skip amount-related params (will be replaced with result values)
      if (!['amount', 'inputMint', 'outputMint', 'side'].includes(key)) {
        reversalParams[key] = value;
      }
    }

    // Map result fields to reversal params
    for (const [targetParam, sourceField] of Object.entries(reversibility.paramMapping)) {
      if (executionResult && sourceField in executionResult) {
        const value = executionResult[sourceField as keyof typeof executionResult];
        if (value !== undefined) {
          reversalParams[targetParam] = value;
        }
      }
    }

    // Special handling for swap: swap input/output mints
    if (actionType === 'swap') {
      reversalParams['inputMint'] = originalParams['outputMint'];
      reversalParams['outputMint'] = originalParams['inputMint'];
    }

    // Special handling for perp_open: flip side
    if (actionType === 'perp_open') {
      const originalSide = originalParams['side'] as string;
      reversalParams['side'] = originalSide === 'long' ? 'short' : 'long';
    }

    return reversalParams;
  }

  private generateId(): string {
    return `${Date.now().toString(36)}-${Math.random().toString(36).substring(2, 10)}`;
  }

  private loadFromStorage(): void {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const data = JSON.parse(stored);
        const snapshots = new Map<string, ActionSnapshot>();

        for (const snapshot of data.snapshots ?? []) {
          snapshots.set(snapshot.id, snapshot);
        }

        this.snapshots$.next(snapshots);
      }
    } catch (err) {
      console.error('Failed to load snapshots from storage:', err);
    }
  }

  private saveToStorage(): void {
    try {
      const data = {
        version: '1.0.0',
        snapshots: Array.from(this.snapshots$.value.values()),
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch (err) {
      console.error('Failed to save snapshots to storage:', err);
    }
  }

  private cleanupExpired(): void {
    const cutoff = Date.now() - TTL_HOURS * 60 * 60 * 1000;
    const current = new Map(this.snapshots$.value);
    let changed = false;

    for (const [id, snapshot] of current) {
      if (snapshot.timestamp < cutoff) {
        current.delete(id);
        changed = true;
      }
    }

    if (changed) {
      this.snapshots$.next(current);
      this.saveToStorage();
    }
  }
}

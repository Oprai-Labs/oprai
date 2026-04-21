/**
 * SpendingLimitService
 *
 * User-defined spending limits:
 *   - maxPerTxUsd  : max USD per single transaction (0 = unlimited)
 *   - maxPerDayUsd : daily cumulative max USD (0 = unlimited)
 *
 * Limits are persisted server-side in auth_schema.spending_limits (enforced by
 * the backend before building any transaction). The frontend also maintains
 * local state for immediate UX feedback.
 *
 * Daily spending accumulator is still in localStorage (resets at midnight).
 */

import { Injectable, inject } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { ApiService } from './api.service';

export interface SpendingLimits {
  maxPerTxUsd: number;
  maxPerDayUsd: number;
}

export interface LimitCheckResult {
  allowed: boolean;
  /** 'per_tx' | 'daily' | undefined (allowed) */
  reason?: 'per_tx' | 'daily';
  limitUsd?: number;
  currentDailyUsd?: number;
}

interface DailyRecord {
  dateKey: string; // 'YYYY-MM-DD'
  totalUsd: number;
}

const DAILY_KEY = 'oprai:spending-daily';

const DEFAULT_LIMITS: SpendingLimits = {
  maxPerTxUsd: 0,   // unlimited by default
  maxPerDayUsd: 0,  // unlimited by default
};

@Injectable({ providedIn: 'root' })
export class SpendingLimitService {
  private readonly api = inject(ApiService);
  private readonly _limits$ = new BehaviorSubject<SpendingLimits>({ ...DEFAULT_LIMITS });
  readonly limits$ = this._limits$.asObservable();

  constructor() {
    // Load server-side limits on service init. Silently falls back to defaults
    // if unauthenticated or the request fails.
    this.api.get<SpendingLimits>('/users/me/spending-limits').subscribe({
      next: (limits) => this._limits$.next({ ...DEFAULT_LIMITS, ...limits }),
      error: () => { /* unauthenticated or network error — use defaults */ },
    });
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  getLimits(): SpendingLimits {
    return this._limits$.value;
  }

  setLimits(limits: SpendingLimits): void {
    // Optimistically update local state, then persist to backend.
    this._limits$.next({ ...limits });
    this.api.put<SpendingLimits>('/users/me/spending-limits', limits).subscribe({
      next: (saved) => this._limits$.next({ ...DEFAULT_LIMITS, ...saved }),
      error: (err) => console.warn('[SpendingLimitService] Failed to save limits to backend', err),
    });
  }

  /**
   * Check limits before executing a transaction.
   * amountUsd = 0 veya negatif ise her zaman izin verir.
   */
  check(amountUsd: number): LimitCheckResult {
    if (amountUsd <= 0) return { allowed: true };

    const { maxPerTxUsd, maxPerDayUsd } = this._limits$.value;

    // Per-transaction check
    if (maxPerTxUsd > 0 && amountUsd > maxPerTxUsd) {
      return { allowed: false, reason: 'per_tx', limitUsd: maxPerTxUsd };
    }

    // Daily check
    if (maxPerDayUsd > 0) {
      const daily = this.getDailyRecord();
      const projected = daily.totalUsd + amountUsd;
      if (projected > maxPerDayUsd) {
        return {
          allowed: false,
          reason: 'daily',
          limitUsd: maxPerDayUsd,
          currentDailyUsd: daily.totalUsd,
        };
      }
    }

    return { allowed: true };
  }

  /**
   * Record daily spending after a successful transaction.
   */
  record(amountUsd: number): void {
    if (amountUsd <= 0) return;
    const daily = this.getDailyRecord();
    daily.totalUsd += amountUsd;
    localStorage.setItem(DAILY_KEY, JSON.stringify(daily));
  }

  getDailySpend(): number {
    return this.getDailyRecord().totalUsd;
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private getDailyRecord(): DailyRecord {
    const today = new Date().toISOString().slice(0, 10); // 'YYYY-MM-DD'
    try {
      const raw = localStorage.getItem(DAILY_KEY);
      if (raw) {
        const parsed: DailyRecord = JSON.parse(raw);
        if (parsed.dateKey === today) return parsed;
      }
    } catch { /* ignore */ }
    // New day → fresh record
    return { dateKey: today, totalUsd: 0 };
  }
}

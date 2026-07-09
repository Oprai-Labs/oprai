import { Injectable, inject, signal, computed, DestroyRef } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';

export interface UsageCounter {
  used: number;
  cap: number;
}

/** Snapshot for ONE timeframe (daily / weekly / monthly). */
export interface UsageTimeframe {
  messages: UsageCounter;
  tokens:   UsageCounter;
  resetsAt: string;
}

/** Per-chat cap reference values (no live counters — the active chat's
 *  state comes from the session list / chat-shell, not /usage). */
export interface ChatCaps {
  messageCap: number;
  tokenCap:   number;
}

export interface UsageSnapshot {
  daily:   UsageTimeframe;
  weekly:  UsageTimeframe;
  monthly: UsageTimeframe;
  chat:    ChatCaps;
}

@Injectable({ providedIn: 'root' })
export class UsageService {
  private readonly api = inject(ApiService);

  private readonly _snapshot = signal<UsageSnapshot | null>(null);
  private readonly _loading = signal(false);
  private readonly _error = signal<string | null>(null);

  readonly snapshot = this._snapshot.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly error = this._error.asReadonly();

  /** Helper: compute pct (0-100) for any counter. */
  private pct(c: UsageCounter | undefined): number {
    if (!c || c.cap <= 0) return 0;
    return Math.min(100, Math.round((c.used / c.cap) * 100));
  }

  readonly dailyMessagePct   = computed(() => this.pct(this._snapshot()?.daily.messages));
  readonly dailyTokenPct     = computed(() => this.pct(this._snapshot()?.daily.tokens));
  readonly weeklyMessagePct  = computed(() => this.pct(this._snapshot()?.weekly.messages));
  readonly weeklyTokenPct    = computed(() => this.pct(this._snapshot()?.weekly.tokens));
  readonly monthlyMessagePct = computed(() => this.pct(this._snapshot()?.monthly.messages));
  readonly monthlyTokenPct   = computed(() => this.pct(this._snapshot()?.monthly.tokens));

  async refresh(): Promise<void> {
    if (this._loading()) return;
    this._loading.set(true);
    this._error.set(null);
    try {
      const data = await firstValueFrom(this.api.get<UsageSnapshot>('/usage'));
      this._snapshot.set(data);
    } catch (err) {
      this._error.set(err instanceof Error ? err.message : 'Failed to load usage');
    } finally {
      this._loading.set(false);
    }
  }

  /** Start polling. Returns a teardown function. */
  startPolling(intervalMs = 30_000, destroyRef?: DestroyRef): () => void {
    void this.refresh();
    const handle = setInterval(() => void this.refresh(), intervalMs);
    const stop = () => clearInterval(handle);
    destroyRef?.onDestroy(stop);
    return stop;
  }
}

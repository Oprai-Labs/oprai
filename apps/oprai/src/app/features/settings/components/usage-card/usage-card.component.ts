import { Component, OnInit, OnDestroy, DestroyRef, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { UsageService, UsageTimeframe, UsageCounter } from '@core/services/usage.service';
import { TPipe } from '@core/i18n';

interface TimeframeRow {
  key: 'daily' | 'weekly' | 'monthly';
  title: string;
}

@Component({
  selector: 'app-usage-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TPipe],
  templateUrl: './usage-card.component.html',
  styleUrl: './usage-card.component.scss',
})
export class UsageCardComponent implements OnInit, OnDestroy {
  private readonly usage = inject(UsageService);
  private readonly destroyRef = inject(DestroyRef);
  private stopPolling: (() => void) | null = null;
  private tickTimer: ReturnType<typeof setInterval> | null = null;

  readonly snapshot = this.usage.snapshot;
  readonly loading = this.usage.loading;
  readonly error = this.usage.error;

  readonly rows: TimeframeRow[] = [
    { key: 'daily',   title: 'Daily usage'   },
    { key: 'weekly',  title: 'Weekly usage'  },
    { key: 'monthly', title: 'Monthly usage' },
  ];

  /** Live "now" tick so resets-in countdowns update without a network poll. */
  private readonly now = signal(Date.now());

  ngOnInit(): void {
    this.stopPolling = this.usage.startPolling(30_000, this.destroyRef);
    this.tickTimer = setInterval(() => this.now.set(Date.now()), 1000);
  }

  ngOnDestroy(): void {
    this.stopPolling?.();
    if (this.tickTimer) clearInterval(this.tickTimer);
  }

  getTimeframe(key: 'daily' | 'weekly' | 'monthly'): UsageTimeframe | null {
    const s = this.snapshot();
    return s ? s[key] : null;
  }

  /** Single "usage %" per timeframe — takes the max of message-% and token-%
   *  so whichever counter is closer to full drives the bar. The user does
   *  not need to know which one (we deliberately hide the message vs token
   *  distinction; the implementation detail belongs in the backend). */
  unifiedPct(tf: UsageTimeframe | null): number {
    if (!tf) return 0;
    const msgPct = this._pct(tf.messages);
    const tokPct = this._pct(tf.tokens);
    return Math.max(msgPct, tokPct);
  }

  private _pct(c: UsageCounter | undefined): number {
    if (!c || c.cap <= 0) return 0;
    return Math.min(100, Math.round((c.used / c.cap) * 100));
  }

  pctState(p: number): 'normal' | 'warning' | 'over' {
    if (p >= 100) return 'over';
    if (p >= 80)  return 'warning';
    return 'normal';
  }

  resetIn(isoTimestamp: string | undefined): string {
    if (!isoTimestamp) return '';
    const target = Date.parse(isoTimestamp);
    const diff   = target - this.now();
    if (!Number.isFinite(target) || diff <= 0) return 'Resets shortly';
    const totalSec = Math.floor(diff / 1000);
    const days = Math.floor(totalSec / 86400);
    const h    = Math.floor((totalSec % 86400) / 3600);
    const m    = Math.floor((totalSec % 3600) / 60);
    if (days > 0) return `Resets in ${days}d ${h}h`;
    if (h > 0)    return `Resets in ${h}h ${m}m`;
    return `Resets in ${m}m`;
  }
}

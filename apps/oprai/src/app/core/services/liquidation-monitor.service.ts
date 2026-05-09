import { Injectable, inject, OnDestroy } from '@angular/core';
import { Subject, interval, Subscription, firstValueFrom } from 'rxjs';
import { switchMap, catchError } from 'rxjs/operators';
import { EMPTY } from 'rxjs';
import { ApiService } from '@core/services/api.service';

export interface LiquidationAlert {
  protocol: string;
  market: string;
  healthRatio: number;
  threshold: number;
  severity: 'warning' | 'danger' | 'critical';
  message: string;
  timestamp: Date;
}

interface LiquidationPosition {
  protocol: string;
  market: string;
  healthRatio: number;
}

interface LiquidationResponse {
  positions: LiquidationPosition[];
}

@Injectable({ providedIn: 'root' })
export class LiquidationMonitorService implements OnDestroy {
  private readonly api = inject(ApiService);

  private readonly alertsSubject = new Subject<LiquidationAlert>();
  private monitorSub: Subscription | null = null;

  /** Observable that emits when a position approaches liquidation */
  readonly liquidationAlerts$ = this.alertsSubject.asObservable();

  /** Start monitoring every 60 seconds (default) */
  startMonitoring(intervalMs = 60_000): void {
    if (this.monitorSub) return;
    this.monitorSub = interval(intervalMs).pipe(
      switchMap(() => this.checkAllPositions()),
      catchError(() => EMPTY),
    ).subscribe();
  }

  stopMonitoring(): void {
    this.monitorSub?.unsubscribe();
    this.monitorSub = null;
  }

  ngOnDestroy(): void {
    this.stopMonitoring();
  }

  /** One-time manual check — resolves with any alerts found */
  async checkNow(): Promise<LiquidationAlert[]> {
    const collected: LiquidationAlert[] = [];
    const sub = this.alertsSubject.subscribe(a => collected.push(a));
    try {
      await this.checkPositions();
    } finally {
      sub.unsubscribe();
    }
    return collected;
  }

  private async checkAllPositions(): Promise<void> {
    await this.checkPositions();
  }

  private async checkPositions(): Promise<void> {
    try {
      // All third-party DeFi API calls are proxied through the backend so the
      // user's wallet address is not exposed in external server logs.
      const res = await firstValueFrom(
        this.api.get<LiquidationResponse>('/defi/liquidations')
      );
      for (const pos of res.positions ?? []) {
        this.maybeEmit(pos.protocol, pos.market, pos.healthRatio);
      }
    } catch (err) {
      console.warn('[LiquidationMonitor] Backend check failed', err);
    }
  }

  private maybeEmit(protocol: string, market: string, healthRatio: number): void {
    let severity: LiquidationAlert['severity'] | null = null;
    let threshold = 0;

    if (healthRatio < 0.8) {
      severity = 'critical';
      threshold = 0.8;
    } else if (healthRatio < 1.0) {
      severity = 'danger';
      threshold = 1.0;
    } else if (healthRatio < 1.2) {
      severity = 'warning';
      threshold = 1.2;
    }

    if (!severity) return;

    const messages: Record<LiquidationAlert['severity'], string> = {
      critical: 'LIQUIDATION IMMINENT — add collateral immediately',
      danger: 'Health ratio low — add collateral soon',
      warning: 'Health ratio approaching threshold — monitor closely',
    };

    this.alertsSubject.next({
      protocol,
      market,
      healthRatio,
      threshold,
      severity,
      message: `${protocol} (${market}): ${(healthRatio * 100).toFixed(1)}% — ${messages[severity]}`,
      timestamp: new Date(),
    });
  }
}

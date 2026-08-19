import { Component, inject, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { Router } from '@angular/router';
import { PositionMonitorService, MonitoredPosition } from '@core/services/position-monitor.service';
import { toSignal } from '@angular/core/rxjs-interop';

@Component({
  selector: 'app-liquidation-alert-banner',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './liquidation-alert-banner.component.html',
  styleUrl: './liquidation-alert-banner.component.scss',
})
export class LiquidationAlertBannerComponent {
  private readonly monitor = inject(PositionMonitorService);
  private readonly router = inject(Router);

  readonly summary = toSignal(this.monitor.summary$, {
    initialValue: {
      positions: [],
      worstRisk: 'safe' as const,
      dangerCount: 0,
      warningCount: 0,
      idleCount: 0,
      lastPolledAt: null,
      polling: false,
    },
  });

  readonly visible = computed(() => {
    const s = this.summary();
    return s.dangerCount > 0 || s.warningCount > 0 || s.idleCount > 0;
  });

  /**
   * Liquidity positions that have drifted out of their range.
   *
   * Kept apart from the liquidation levels, and shown in the calm tone,
   * because nothing here is at risk — the money has simply stopped working.
   * Raising the red banner for an idle position would teach people that the
   * red banner does not mean anything.
   */
  readonly idlePositions = computed<MonitoredPosition[]>(() =>
    this.summary().positions.filter(p => p.kind === 'lp' && p.earning === false)
  );

  readonly dangerPositions = computed<MonitoredPosition[]>(() =>
    this.summary().positions.filter(p => p.riskLevel === 'danger')
  );

  readonly warningPositions = computed<MonitoredPosition[]>(() =>
    this.summary().positions.filter(p => p.riskLevel === 'warning')
  );

  readonly bannerLevel = computed(() => {
    const s = this.summary();
    if (s.dangerCount > 0) return 'danger';
    if (s.warningCount > 0) return 'warning';
    return 'idle';
  });

  readonly primaryPosition = computed<MonitoredPosition | null>(() => {
    const danger = this.dangerPositions();
    const warning = this.warningPositions();
    return danger[0] ?? warning[0] ?? null;
  });

  readonly extraCount = computed(() => {
    const total = this.summary().dangerCount + this.summary().warningCount;
    return total > 1 ? total - 1 : 0;
  });

  navigateToPortfolio(): void {
    this.router.navigate(['/portfolio']);
  }

  refresh(): void {
    this.monitor.refresh();
  }

  formatProtocol(protocol: string): string {
    return protocol.charAt(0).toUpperCase() + protocol.slice(1);
  }

  formatHf(hf: number): string {
    return hf > 50 ? '∞' : hf.toFixed(2);
  }
}

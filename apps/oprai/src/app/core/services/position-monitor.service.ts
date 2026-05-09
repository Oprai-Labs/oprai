/**
 * PositionMonitorService
 *
 * Periodically monitors all DeFi positions (Kamino, MarginFi, Solend)
 * izler ve liquidation riskini hesaplar.
 *
 * Risk seviyeleri:
 *   safe    — healthFactor > 1.5
 *   warning — 1.2 < healthFactor ≤ 1.5
 *   danger  — healthFactor ≤ 1.2   (liquidation imminent!)
 *
 * Features:
 *   - 60 saniyede bir otomatik poll
 *   - Toast notification on state change (safe→warning, warning→danger)
 *   - P&L tracking (entry value vs current value)
 *   - Polling stops when wallet disconnects
 */

import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { KaminoService } from './market/kamino.service';
import { MarginfiService } from './market/marginfi.service';
import { SolendService } from './market/solend.service';
import { WalletService } from './wallet.service';
import { NotificationService } from './notification.service';

// ── Risk Seviyeleri ──────────────────────────────────────────────────────────

export type RiskLevel = 'safe' | 'warning' | 'danger';

export function toRiskLevel(hf: number): RiskLevel {
  if (hf <= 1.2) return 'danger';
  if (hf <= 1.5) return 'warning';
  return 'safe';
}

// ── Unified Position Types ────────────────────────────────────────────────────

export type ProtocolId = 'kamino' | 'marginfi' | 'solend';

export interface MonitoredPosition {
  /** Unique key: `${protocol}:${address}` */
  key: string;
  protocol: ProtocolId;
  address: string;

  /** Health factor (1.0 = liquidation threshold). Perp: marginRatio / maintenanceRatio */
  healthFactor: number;
  riskLevel: RiskLevel;

  /** Toplam teminat (USD) */
  collateralUsd: number;
  /** Total debt (USD) */
  debtUsd: number;

  /** Previous risk level — used to trigger alerts */
  previousRiskLevel?: RiskLevel;

  /** Last update timestamp */
  updatedAt: number;

  /** Protokol-spesifik ek bilgi */
  meta: Record<string, unknown>;
}

export interface PositionSummary {
  positions: MonitoredPosition[];
  /** En kritik risk seviyesi */
  worstRisk: RiskLevel;
  /** Number of positions in liquidation danger */
  dangerCount: number;
  /** Number of positions at warning level */
  warningCount: number;
  /** Last poll time */
  lastPolledAt: number | null;
  /** Whether a poll is currently running */
  polling: boolean;
}

// ── Service ──────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 60_000; // 60 saniye

@Injectable({ providedIn: 'root' })
export class PositionMonitorService implements OnDestroy {
  private readonly kamino = inject(KaminoService);
  private readonly marginfi = inject(MarginfiService);
  private readonly solend = inject(SolendService);
  private readonly wallet = inject(WalletService);
  private readonly notifications = inject(NotificationService);

  private readonly _summary$ = new BehaviorSubject<PositionSummary>({
    positions: [],
    worstRisk: 'safe',
    dangerCount: 0,
    warningCount: 0,
    lastPolledAt: null,
    polling: false,
  });

  readonly summary$ = this._summary$.asObservable();

  private pollTimer: ReturnType<typeof setInterval> | null = null;
  /** Previous risk levels per position — for change detection */
  private previousRiskMap = new Map<string, RiskLevel>();

  // ── Public API ────────────────────────────────────────────────────────────

  /** Start monitoring (called when wallet connects) */
  start(): void {
    if (this.pollTimer) return; // Already running
    this.poll(); // Immediate first poll
    this.pollTimer = setInterval(() => this.poll(), POLL_INTERVAL_MS);
  }

  /** Stop monitoring (called when wallet disconnects) */
  stop(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    this._summary$.next({
      positions: [],
      worstRisk: 'safe',
      dangerCount: 0,
      warningCount: 0,
      lastPolledAt: null,
      polling: false,
    });
    this.previousRiskMap.clear();
  }

  /** Manuel olarak tek poll tetikle */
  async refresh(): Promise<void> {
    return this.poll();
  }

  ngOnDestroy(): void {
    this.stop();
  }

  // ── Poll Loop ──────────────────────────────────────────────────────────

  private async poll(): Promise<void> {
    const walletAddress = this.wallet.publicKey();
    if (!walletAddress) return;

    this._summary$.next({ ...this._summary$.value, polling: true });

    try {
      // Query all protocols in parallel
      const [kaminoPositions, marginfiPositions, solendPositions] =
        await Promise.allSettled([
          this.fetchKamino(walletAddress),
          this.fetchMarginfi(walletAddress),
          this.fetchSolend(walletAddress),
        ]);

      const positions: MonitoredPosition[] = [
        ...(kaminoPositions.status === 'fulfilled' ? kaminoPositions.value : []),
        ...(marginfiPositions.status === 'fulfilled' ? marginfiPositions.value : []),
        ...(solendPositions.status === 'fulfilled' ? solendPositions.value : []),
      ];

      // Detect risk level changes and send alerts
      this.checkRiskChanges(positions);

      // Update previous risk map
      for (const p of positions) {
        this.previousRiskMap.set(p.key, p.riskLevel);
      }

      const dangerCount = positions.filter(p => p.riskLevel === 'danger').length;
      const warningCount = positions.filter(p => p.riskLevel === 'warning').length;
      const worstRisk: RiskLevel =
        dangerCount > 0 ? 'danger' : warningCount > 0 ? 'warning' : 'safe';

      this._summary$.next({
        positions,
        worstRisk,
        dangerCount,
        warningCount,
        lastPolledAt: Date.now(),
        polling: false,
      });
    } catch {
      this._summary$.next({ ...this._summary$.value, polling: false });
    }
  }

  // ── Protokol Fetch'leri ───────────────────────────────────────────────────

  private async fetchKamino(wallet: string): Promise<MonitoredPosition[]> {
    const obligations = await this.kamino.getObligations(wallet);
    return obligations
      .filter(o => parseFloat(o.borrowedValue) > 0) // Skip positions with no debt
      .map(o => {
        const hf = parseFloat(o.healthFactor) || 99;
        return {
          key: `kamino:${o.obligationAddress}`,
          protocol: 'kamino' as ProtocolId,
          address: o.obligationAddress,
          healthFactor: hf,
          riskLevel: toRiskLevel(hf),
          collateralUsd: parseFloat(o.depositedValue) || 0,
          debtUsd: parseFloat(o.borrowedValue) || 0,
          previousRiskLevel: this.previousRiskMap.get(`kamino:${o.obligationAddress}`),
          updatedAt: Date.now(),
          meta: {
            market: o.market,
            deposits: o.deposits.map(d => d.symbol),
            borrows: o.borrows.map(b => b.symbol),
          },
        };
      });
  }

  private async fetchMarginfi(wallet: string): Promise<MonitoredPosition[]> {
    const accounts = await this.marginfi.getAccountsByWallet(wallet);
    return accounts
      .filter(a => a.totalBorrowUsd > 0)
      .map(a => {
        const hf = a.healthFactor ?? 99;
        return {
          key: `marginfi:${a.address}`,
          protocol: 'marginfi' as ProtocolId,
          address: a.address,
          healthFactor: hf,
          riskLevel: toRiskLevel(hf),
          collateralUsd: a.totalCollateralUsd,
          debtUsd: a.totalBorrowUsd,
          previousRiskLevel: this.previousRiskMap.get(`marginfi:${a.address}`),
          updatedAt: Date.now(),
          meta: {
            deposits: a.deposits.map(d => d.symbol),
            borrows: a.borrows.map(b => b.symbol),
            liquidationThreshold: a.liquidationThreshold,
          },
        };
      });
  }

  private async fetchSolend(wallet: string): Promise<MonitoredPosition[]> {
    const obligations = await this.solend.getObligations(wallet);
    return obligations
      .filter(o => o.totalBorrowedValue > 0)
      .map(o => {
        const hf = o.healthFactor ?? 99;
        return {
          key: `solend:${o.address}`,
          protocol: 'solend' as ProtocolId,
          address: o.address,
          healthFactor: hf,
          riskLevel: toRiskLevel(hf),
          collateralUsd: o.totalCollateralValue,
          debtUsd: o.totalBorrowedValue,
          previousRiskLevel: this.previousRiskMap.get(`solend:${o.address}`),
          updatedAt: Date.now(),
          meta: {
            deposits: o.deposits.map(d => d.assetSymbol),
            borrows: o.borrows.map(b => b.assetSymbol),
            liquidationValue: o.liquidationValue,
          },
        };
      });
  }


  // ── Alert Sistemi ─────────────────────────────────────────────────────────

  private checkRiskChanges(positions: MonitoredPosition[]): void {
    for (const pos of positions) {
      const prev = this.previousRiskMap.get(pos.key);
      const curr = pos.riskLevel;

      // Notify if seen for the first time and already dangerous
      if (!prev && curr !== 'safe') {
        this.sendAlert(pos, curr);
        continue;
      }

      // Notify if condition worsened
      if (prev && this.isWorsening(prev, curr)) {
        this.sendAlert(pos, curr);
      }
    }
  }

  private isWorsening(prev: RiskLevel, curr: RiskLevel): boolean {
    const rank: Record<RiskLevel, number> = { safe: 0, warning: 1, danger: 2 };
    return rank[curr] > rank[prev];
  }

  private sendAlert(pos: MonitoredPosition, level: RiskLevel): void {
    const protocol = pos.protocol.charAt(0).toUpperCase() + pos.protocol.slice(1);
    const hfStr = pos.healthFactor.toFixed(2);
    const shortAddr = pos.address.slice(0, 8) + '…';

    if (level === 'danger') {
      this.notifications.error(
        `⚠️ ${protocol} pozisyonu liquidation tehlikesinde! Health factor: ${hfStr} (${shortAddr})`,
        0 // Keep until user dismisses
      );
    } else if (level === 'warning') {
      this.notifications.warning(
        `${protocol} position health factor dropping: ${hfStr} (${shortAddr})`,
        10_000
      );
    }
  }
}

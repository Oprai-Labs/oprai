/**
 * PositionMonitorService
 *
 * Periodically monitors all DeFi positions (Kamino, Solend)
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
import { SolendService } from './market/solend.service';
import { RaydiumService } from './market/raydium.service';
import { OrcaService } from './market/orca.service';
import { MeteoraService } from './market/meteora.service';
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

export type ProtocolId = 'kamino' | 'solend' | 'raydium' | 'orca' | 'meteora';

export interface MonitoredPosition {
  /** Unique key: `${protocol}:${address}` */
  key: string;
  protocol: ProtocolId;
  address: string;

  /**
   * What can go wrong with this position, which is not the same thing for
   * every protocol.
   *
   * A borrow can be liquidated; a concentrated liquidity position cannot. It
   * simply stops earning when the price leaves its band, and keeps sitting
   * there until someone moves it. Both need watching, but sounding the
   * liquidation alarm for a position that is merely idle teaches people to
   * ignore the alarm.
   */
  kind: 'debt' | 'lp';

  /** LP only: false once the price has left the band and fees have stopped. */
  earning?: boolean;
  /** LP only: the pair, for a message that names what stopped. */
  pairLabel?: string;

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
  /**
   * Liquidity positions sitting outside their range.
   *
   * Counted apart from the risk levels on purpose: nothing is at risk, money
   * is simply not working. It deserves a quieter surface than a liquidation
   * warning, and the two must not be added together.
   */
  idleCount: number;
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
  private readonly solend = inject(SolendService);
  private readonly raydium = inject(RaydiumService);
  private readonly orca = inject(OrcaService);
  private readonly meteora = inject(MeteoraService);
  private readonly wallet = inject(WalletService);
  private readonly notifications = inject(NotificationService);

  private readonly _summary$ = new BehaviorSubject<PositionSummary>({
    positions: [],
    worstRisk: 'safe',
    dangerCount: 0,
    warningCount: 0,
    idleCount: 0,
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
      idleCount: 0,
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
      // Query all protocols in parallel.
      //
      // Solend is gated off until its backend read lands: SolendService hits
      // `api.solend.fi/v1/...` directly from the browser and Solend Labs took
      // the public API offline, so every poll 404'd. The error was silenced at
      // the service layer (try/catch → []) so the monitor "worked" while the
      // 60s poll spammed the console. Re-enable when the read path is
      // implemented (on-chain account scan in solana-service-rs).
      const [kaminoPositions, lpPositions] = await Promise.allSettled([
        this.fetchKamino(walletAddress),
        // this.fetchSolend(walletAddress),
        this.fetchLiquidityPositions(walletAddress),
      ]);

      const positions: MonitoredPosition[] = [
        ...(kaminoPositions.status === 'fulfilled' ? kaminoPositions.value : []),
        ...(lpPositions.status === 'fulfilled' ? lpPositions.value : []),
      ];

      // Detect risk level changes and send alerts
      this.checkRiskChanges(positions);

      // Update previous risk map
      for (const p of positions) {
        this.previousRiskMap.set(p.key, p.riskLevel);
      }

      const dangerCount = positions.filter(p => p.riskLevel === 'danger').length;
      const warningCount = positions.filter(p => p.riskLevel === 'warning').length;
      const idleCount = positions.filter(p => p.kind === 'lp' && p.earning === false).length;
      const worstRisk: RiskLevel =
        dangerCount > 0 ? 'danger' : warningCount > 0 ? 'warning' : 'safe';

      this._summary$.next({
        positions,
        worstRisk,
        dangerCount,
        warningCount,
        idleCount,
        lastPolledAt: Date.now(),
        polling: false,
      });
    } catch {
      this._summary$.next({ ...this._summary$.value, polling: false });
    }
  }

  // ── Protokol Fetch'leri ───────────────────────────────────────────────────

  /**
   * Concentrated liquidity positions, and whether they are still earning.
   *
   * A position outside its band is not in danger — it is idle, which is why
   * these carry riskLevel 'safe' and are counted separately. The loss is
   * invisible by design: the balance still shows, the position still exists,
   * and the fees simply stop. Nobody finds out unless they are told.
   *
   * Each protocol already reports `inRange` on the user's positions, so this
   * reads it rather than recomputing bands from ticks and bins.
   */
  private async fetchLiquidityPositions(wallet: string): Promise<MonitoredPosition[]> {
    const [ray, orca, met] = await Promise.allSettled([
      this.raydium.getClmmPositions(wallet),
      this.orca.getPositions(wallet),
      this.meteora.getPositions(wallet),
    ]);
    const out: MonitoredPosition[] = [];
    const push = (
      protocol: ProtocolId,
      address: string,
      inRange: boolean,
      pairLabel: string,
      meta: Record<string, unknown>,
    ) => {
      if (!address) return;
      out.push({
        key: `${protocol}:${address}`,
        protocol,
        address,
        kind: 'lp',
        earning: inRange,
        pairLabel,
        // Nothing here can be liquidated, so the risk levels the banner keys
        // off stay clear for these.
        healthFactor: Number.POSITIVE_INFINITY,
        riskLevel: 'safe',
        collateralUsd: 0,
        debtUsd: 0,
        updatedAt: Date.now(),
        meta,
      });
    };

    if (ray.status === 'fulfilled') {
      for (const p of ray.value ?? []) {
        push('raydium', p.nftMint, p.inRange, `${p.tokenA}/${p.tokenB}`, { poolId: p.poolId });
      }
    }
    if (orca.status === 'fulfilled') {
      for (const p of orca.value ?? []) {
        // Orca's list type marks inRange optional; absent means unknown, and
        // an unknown must not be reported as "stopped earning".
        if (typeof p.inRange !== 'boolean') continue;
        push('orca', p.address, p.inRange, 'Orca position', { whirlpool: p.whirlpool });
      }
    }
    if (met.status === 'fulfilled') {
      for (const p of met.value ?? []) {
        push('meteora', p.address, p.inRange, 'Meteora position', { pool: p.pool });
      }
    }
    return out;
  }

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
          kind: 'debt' as const,
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


  private async fetchSolend(wallet: string): Promise<MonitoredPosition[]> {
    const obligations = await this.solend.getObligations(wallet);
    return obligations
      .filter(o => o.totalBorrowedValue > 0)
      .map(o => {
        const hf = o.healthFactor ?? 99;
        return {
          key: `solend:${o.address}`,
          kind: 'debt' as const,
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

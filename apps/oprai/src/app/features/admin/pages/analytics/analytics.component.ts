import { Component, inject, OnInit, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminApiService, AnalyticsTimeseries, DashboardStats } from '../../services/admin-api.service';
import { LucideAngularModule } from 'lucide-angular';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonStatCardsComponent } from '@shared/components/skeletons/skeleton-stat-cards.component';
import { TPipe } from '@core/i18n';

interface TimeseriesRow {
  date: string;
  users: number;
  sessions: number;
  tx: number;
  messages: number;
}

interface ProtocolRow {
  action: string;
  count: number;
}

interface TopWalletRow {
  wallet: string;
  sessions?: number;
  messages?: number;
  transactions?: number;
}

@Component({
  selector: 'app-analytics',
  standalone: true,
  imports: [CommonModule,
    FormsModule,
    LucideAngularModule,
    AdminLayoutComponent,
    SkeletonStatCardsComponent, TPipe],
  templateUrl: './analytics.component.html',
  styleUrl: './analytics.component.scss',
})
export class AnalyticsComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly selectedDays = signal<7 | 30 | 90>(30);
  readonly timeseries = signal<TimeseriesRow[]>([]);
  readonly timeseriesLoading = signal(true);
  readonly timeseriesError = signal<string | null>(null);

  readonly protocolData = signal<ProtocolRow[]>([]);
  readonly protocolLoading = signal(true);

  readonly topWallets = signal<TopWalletRow[]>([]);
  readonly walletsLoading = signal(true);

  readonly stats = signal<DashboardStats | null>(null);
  readonly statsLoading = signal(true);

  readonly maxTx = computed(() => {
    const rows = this.timeseries();
    if (!rows.length) return 1;
    return Math.max(...rows.map((r) => r.tx), 1);
  });

  readonly maxUsers = computed(() => {
    const rows = this.timeseries();
    if (!rows.length) return 1;
    return Math.max(...rows.map((r) => r.users), 1);
  });

  readonly maxProtocolCount = computed(() => {
    const rows = this.protocolData();
    if (!rows.length) return 1;
    return Math.max(...rows.map((r) => r.count), 1);
  });

  ngOnInit(): void {
    this.loadAll();
  }

  loadAll(): void {
    this.loadTimeseries();
    this.loadProtocol();
    this.loadTopWallets();
    this.loadStats();
  }

  setDays(days: 7 | 30 | 90): void {
    this.selectedDays.set(days);
    this.loadTimeseries();
  }

  loadTimeseries(): void {
    this.timeseriesLoading.set(true);
    this.timeseriesError.set(null);
    this.adminApi.getAnalyticsTimeseries(this.selectedDays()).subscribe({
      next: (data: AnalyticsTimeseries) => {
        this.timeseries.set(data.timeseries);
        this.timeseriesLoading.set(false);
      },
      error: () => {
        this.timeseriesError.set('Failed to load timeseries data.');
        this.timeseriesLoading.set(false);
      },
    });
  }

  loadProtocol(): void {
    this.protocolLoading.set(true);
    this.adminApi.getTxByProtocol().subscribe({
      next: (res) => {
        this.protocolData.set(res.data);
        this.protocolLoading.set(false);
      },
      error: () => {
        this.protocolLoading.set(false);
      },
    });
  }

  loadTopWallets(): void {
    this.walletsLoading.set(true);
    this.adminApi.getTopWallets(10).subscribe({
      next: (res) => {
        this.topWallets.set(res.data as TopWalletRow[]);
        this.walletsLoading.set(false);
      },
      error: () => {
        this.walletsLoading.set(false);
      },
    });
  }

  loadStats(): void {
    this.statsLoading.set(true);
    this.adminApi.getDashboardStats().subscribe({
      next: (stats) => {
        this.stats.set(stats);
        this.statsLoading.set(false);
      },
      error: () => {
        this.statsLoading.set(false);
      },
    });
  }

  barWidth(value: number, max: number): string {
    if (max === 0) return '0%';
    return `${Math.max((value / max) * 100, 2)}%`;
  }

  formatDate(dateStr: string): string {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  truncateWallet(wallet: string): string {
    if (!wallet || wallet.length <= 12) return wallet;
    return `${wallet.slice(0, 6)}…${wallet.slice(-4)}`;
  }

  logout(): void {
    this.adminApi.adminLogout().subscribe({
      complete: () => { window.location.href = '/admin/login'; },
      error: () => { window.location.href = '/admin/login'; },
    });
  }
}

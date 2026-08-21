import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminApiService, IPLog, IPWalletSummary, PaginatedResponse } from '../../services/admin-api.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { LucideAngularModule } from 'lucide-angular';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-ip-logs',
  standalone: true,
  imports: [CommonModule,
    FormsModule,
    TruncateAddressPipe,
    TimeAgoPipe,
    LucideAngularModule,
    AdminLayoutComponent,
    SkeletonTableComponent, TPipe],
  templateUrl: './ip-logs.component.html',
  styleUrl: './ip-logs.component.scss',
})
export class IpLogsComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  activeTab = signal<'all' | 'suspicious'>('all');

  // All Logs tab
  readonly logs = signal<IPLog[]>([]);
  readonly logsLoading = signal(true);
  readonly logsError = signal<string | null>(null);
  readonly totalCount = signal(0);
  readonly page = signal(1);
  readonly pageSize = 25;

  // Suspicious IPs tab
  readonly suspicious = signal<IPWalletSummary[]>([]);
  readonly suspiciousLoading = signal(false);
  readonly suspiciousError = signal<string | null>(null);
  readonly suspiciousLoaded = signal(false);

  // Filters
  filterIp = '';
  filterWallet = '';
  filterSuccess = '';
  dateFrom = '';
  dateTo = '';

  ngOnInit(): void {
    this.loadLogs();
  }

  switchTab(tab: 'all' | 'suspicious'): void {
    this.activeTab.set(tab);
    if (tab === 'suspicious' && !this.suspiciousLoaded()) {
      this.loadSuspicious();
    }
  }

  loadLogs(): void {
    this.logsLoading.set(true);
    this.logsError.set(null);
    this.adminApi
      .getIPLogs({
        page: this.page(),
        limit: this.pageSize,
        ip: this.filterIp || undefined,
        wallet: this.filterWallet || undefined,
        success: this.filterSuccess || undefined,
        from: this.dateFrom || undefined,
        to: this.dateTo || undefined,
      })
      .subscribe({
        next: (result: PaginatedResponse<IPLog>) => {
          this.logs.set(result.data);
          this.totalCount.set(result.total);
          this.logsLoading.set(false);
        },
        error: () => {
          this.logsError.set('Failed to load IP logs.');
          this.logsLoading.set(false);
        },
      });
  }

  loadSuspicious(): void {
    this.suspiciousLoading.set(true);
    this.suspiciousError.set(null);
    this.adminApi.getSuspiciousIPs(2).subscribe({
      next: (res) => {
        this.suspicious.set(res.data);
        this.suspiciousLoading.set(false);
        this.suspiciousLoaded.set(true);
      },
      error: () => {
        this.suspiciousError.set('Failed to load suspicious IPs.');
        this.suspiciousLoading.set(false);
      },
    });
  }

  onFilterChange(): void {
    this.page.set(1);
    this.loadLogs();
  }

  onPageChange(newPage: number): void {
    this.page.set(newPage);
    this.loadLogs();
  }

  exportLogs(): void {
    this.adminApi.exportData('users').subscribe((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `ip-logs-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  get totalPages(): number {
    return Math.ceil(this.totalCount() / this.pageSize);
  }

  successClass(success: boolean): string {
    return success ? 'badge--success' : 'badge--danger';
  }

  successLabel(success: boolean): string {
    return success ? 'Success' : 'Failed';
  }

  logout(): void {
    this.adminApi.adminLogout().subscribe({
      complete: () => { window.location.href = '/admin/login'; },
      error: () => { window.location.href = '/admin/login'; },
    });
  }
}

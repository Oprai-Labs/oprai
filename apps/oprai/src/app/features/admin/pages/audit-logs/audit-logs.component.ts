import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminApiService, AuditLog, PaginatedResponse } from '../../services/admin-api.service';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { LucideAngularModule } from 'lucide-angular';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-audit-logs',
  standalone: true,
  imports: [CommonModule,
    FormsModule,
    TimeAgoPipe,
    LucideAngularModule,
    AdminLayoutComponent,
    SkeletonTableComponent, TPipe],
  templateUrl: './audit-logs.component.html',
  styleUrl: './audit-logs.component.scss',
})
export class AuditLogsComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly logs = signal<AuditLog[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly totalCount = signal(0);
  readonly page = signal(1);
  readonly pageSize = 25;
  readonly exporting = signal(false);

  filterAdmin = '';
  filterAction = '';
  dateFrom = '';
  dateTo = '';

  readonly actionOptions = [
    '', 'login', 'logout', 'create_user', 'delete_user', 'suspend_user',
    'reset_password', 'export', 'view_logs', 'create_admin', 'delete_admin',
  ];

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminApi
      .getAuditLogs({
        page: this.page(),
        limit: this.pageSize,
        admin: this.filterAdmin || undefined,
        action: this.filterAction || undefined,
        from: this.dateFrom || undefined,
        to: this.dateTo || undefined,
      })
      .subscribe({
        next: (result: PaginatedResponse<AuditLog>) => {
          this.logs.set(result.data);
          this.totalCount.set(result.total);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Failed to load audit logs. Please try again.');
          this.loading.set(false);
        },
      });
  }

  onFilterChange(): void {
    this.page.set(1);
    this.load();
  }

  onPageChange(newPage: number): void {
    this.page.set(newPage);
    this.load();
  }

  exportCSV(): void {
    this.exporting.set(true);
    this.adminApi
      .exportAuditLogs(this.dateFrom || undefined, this.dateTo || undefined)
      .subscribe({
        next: (blob) => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `audit-logs-${new Date().toISOString().slice(0, 10)}.csv`;
          a.click();
          URL.revokeObjectURL(url);
          this.exporting.set(false);
        },
        error: () => {
          this.exporting.set(false);
        },
      });
  }

  actionBadgeClass(action: string): string {
    switch (action?.toLowerCase()) {
      case 'login': return 'badge--info';
      case 'logout': return 'badge--neutral';
      case 'suspend_user':
      case 'delete_user': return 'badge--warning';
      case 'delete_admin': return 'badge--danger';
      case 'export':
      case 'export_data': return 'badge--purple';
      case 'create_user':
      case 'create_admin': return 'badge--success';
      default: return 'badge--neutral';
    }
  }

  get totalPages(): number {
    return Math.ceil(this.totalCount() / this.pageSize);
  }

  logout(): void {
    this.adminApi.adminLogout().subscribe({
      complete: () => { window.location.href = '/admin/login'; },
      error: () => { window.location.href = '/admin/login'; },
    });
  }
}

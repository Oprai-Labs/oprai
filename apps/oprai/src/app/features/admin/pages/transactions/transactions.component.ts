import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminApiService, AdminTransaction, PaginatedResponse } from '../../services/admin-api.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { LucideAngularModule } from 'lucide-angular';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-transactions',
  standalone: true,
  imports: [CommonModule,
    FormsModule,
    TruncateAddressPipe,
    TimeAgoPipe,
    LucideAngularModule,
    AdminLayoutComponent,
    SkeletonTableComponent, TPipe],
  templateUrl: './transactions.component.html',
  styleUrl: './transactions.component.scss',
})
export class TransactionsComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly transactions = signal<AdminTransaction[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly totalCount = signal(0);
  readonly page = signal(1);
  readonly pageSize = 25;

  readonly selectedTx = signal<AdminTransaction | null>(null);
  readonly detailOpen = signal(false);

  searchQuery = '';
  filterStatus = '';
  filterAction = '';

  readonly statusOptions = ['', 'pending', 'confirmed', 'failed', 'error'];
  readonly actionOptions = ['', 'swap', 'transfer', 'stake', 'lend', 'borrow', 'launch'];

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminApi
      .getTransactions({
        page: this.page(),
        limit: this.pageSize,
        status: this.filterStatus || undefined,
        action: this.filterAction || undefined,
        search: this.searchQuery || undefined,
      })
      .subscribe({
        next: (result: PaginatedResponse<AdminTransaction>) => {
          this.transactions.set(result.data);
          this.totalCount.set(result.total);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Failed to load transactions. Please try again.');
          this.loading.set(false);
        },
      });
  }

  onSearch(): void {
    this.page.set(1);
    this.load();
  }

  onFilterChange(): void {
    this.page.set(1);
    this.load();
  }

  onPageChange(newPage: number): void {
    this.page.set(newPage);
    this.load();
  }

  openDetail(tx: AdminTransaction): void {
    this.selectedTx.set(tx);
    this.detailOpen.set(true);
  }

  closeDetail(): void {
    this.detailOpen.set(false);
    this.selectedTx.set(null);
  }

  get totalPages(): number {
    return Math.ceil(this.totalCount() / this.pageSize);
  }

  statusClass(status: string): string {
    switch (status?.toLowerCase()) {
      case 'confirmed': return 'badge--success';
      case 'pending': return 'badge--warning';
      case 'failed':
      case 'error': return 'badge--danger';
      default: return 'badge--neutral';
    }
  }

  formatFee(fee?: number): string {
    if (fee === undefined || fee === null) return '-';
    return `${fee.toFixed(6)} SOL`;
  }

  logout(): void {
    this.adminApi.adminLogout().subscribe({
      complete: () => { window.location.href = '/admin/login'; },
      error: () => { window.location.href = '/admin/login'; },
    });
  }
}

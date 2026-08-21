import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AdminApiService, AdminUser } from '../../services/admin-api.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { LucideAngularModule } from 'lucide-angular';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-users',
  standalone: true,
  imports: [CommonModule,
    FormsModule,
    RouterLink,
    TruncateAddressPipe,
    TimeAgoPipe,
    LucideAngularModule,
    AdminLayoutComponent,
    SkeletonTableComponent, TPipe],
  templateUrl: './users.component.html',
  styleUrl: './users.component.scss',
})
export class UsersComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly users = signal<AdminUser[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly totalCount = signal(0);
  readonly page = signal(1);
  readonly pageSize = 25;

  searchQuery = '';
  sortField = 'createdAt';
  sortOrder: 'asc' | 'desc' = 'desc';

  ngOnInit(): void {
    this.loadUsers();
  }

  loadUsers(): void {
    this.loading.set(true);
    this.adminApi
      .getUsers({
        page: this.page(),
        limit: this.pageSize,
        search: this.searchQuery || undefined,
        sortBy: this.sortField,
        sortOrder: this.sortOrder,
      })
      .subscribe({
        next: (result) => {
          this.users.set(result.users);
          this.totalCount.set(result.total);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Failed to load users');
          this.loading.set(false);
        },
      });
  }

  onSearch(): void {
    this.page.set(1);
    this.loadUsers();
  }

  onSort(field: string): void {
    if (this.sortField === field) {
      this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
    } else {
      this.sortField = field;
      this.sortOrder = 'desc';
    }
    this.loadUsers();
  }

  onPageChange(newPage: number): void {
    this.page.set(newPage);
    this.loadUsers();
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

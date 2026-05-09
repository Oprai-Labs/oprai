import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminApiService, AdminSession, PaginatedResponse } from '../../services/admin-api.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { LucideAngularModule } from 'lucide-angular';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';

@Component({
  selector: 'app-sessions',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    TruncateAddressPipe,
    TimeAgoPipe,
    LucideAngularModule,
    AdminLayoutComponent,
    SkeletonTableComponent,
  ],
  templateUrl: './sessions.component.html',
  styleUrl: './sessions.component.scss',
})
export class SessionsComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly sessions = signal<AdminSession[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly totalCount = signal(0);
  readonly page = signal(1);
  readonly pageSize = 25;

  readonly selectedSession = signal<AdminSession | null>(null);
  readonly sessionMessages = signal<unknown[]>([]);
  readonly messagesLoading = signal(false);
  readonly detailOpen = signal(false);

  searchQuery = '';
  dateFrom = '';
  dateTo = '';

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminApi
      .getSessions({
        page: this.page(),
        limit: this.pageSize,
        search: this.searchQuery || undefined,
        from: this.dateFrom || undefined,
        to: this.dateTo || undefined,
      })
      .subscribe({
        next: (result: PaginatedResponse<AdminSession>) => {
          this.sessions.set(result.data);
          this.totalCount.set(result.total);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Failed to load sessions. Please try again.');
          this.loading.set(false);
        },
      });
  }

  onSearch(): void {
    this.page.set(1);
    this.load();
  }

  onPageChange(newPage: number): void {
    this.page.set(newPage);
    this.load();
  }

  viewMessages(session: AdminSession): void {
    this.selectedSession.set(session);
    this.detailOpen.set(true);
    this.messagesLoading.set(true);
    this.sessionMessages.set([]);
    this.adminApi.getSessionMessages(session.id).subscribe({
      next: (res) => {
        this.sessionMessages.set(res.data);
        this.messagesLoading.set(false);
      },
      error: () => {
        this.messagesLoading.set(false);
      },
    });
  }

  closeDetail(): void {
    this.detailOpen.set(false);
    this.selectedSession.set(null);
    this.sessionMessages.set([]);
  }

  closeSession(session: AdminSession): void {
    if (!confirm(`Close session "${session.title}"?`)) return;
    this.adminApi.closeSession(session.id).subscribe({
      next: () => this.load(),
    });
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

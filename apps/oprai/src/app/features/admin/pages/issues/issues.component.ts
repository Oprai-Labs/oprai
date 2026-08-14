import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { AdminApiService, AdminIssueReport, PaginatedResponse } from '../../services/admin-api.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';

/**
 * The queue for Help → Report Issue.
 *
 * Without this page the reports table is a write-only sink: users would file
 * reports that nobody could read, which is worse than the broken GitHub link
 * it replaced, because it looks like it works.
 */
@Component({
  selector: 'app-admin-issues',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    LucideAngularModule,
    TruncateAddressPipe,
    TimeAgoPipe,
    AdminLayoutComponent,
    SkeletonTableComponent,
  ],
  templateUrl: './issues.component.html',
  styleUrl: './issues.component.scss',
})
export class IssuesComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly reports = signal<AdminIssueReport[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly totalCount = signal(0);
  readonly page = signal(1);
  readonly pageSize = 25;

  readonly selected = signal<AdminIssueReport | null>(null);
  readonly saving = signal(false);

  /** Draft note for the open report — the user sees it on their own page. */
  noteDraft = '';
  statusFilter = '';
  categoryFilter = '';
  searchQuery = '';

  readonly statuses = [
    { id: 'open', label: 'Open' },
    { id: 'in_progress', label: 'In progress' },
    { id: 'resolved', label: 'Resolved' },
    { id: 'dismissed', label: 'Closed' },
  ];

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminApi
      .getIssueReports({
        page: this.page(),
        limit: this.pageSize,
        status: this.statusFilter || undefined,
        category: this.categoryFilter || undefined,
        search: this.searchQuery || undefined,
      })
      .subscribe({
        next: (result: PaginatedResponse<AdminIssueReport>) => {
          this.reports.set(result.data);
          this.totalCount.set(result.total);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Failed to load issue reports. Please try again.');
          this.loading.set(false);
        },
      });
  }

  onFilter(): void {
    this.page.set(1);
    this.load();
  }

  onPageChange(newPage: number): void {
    this.page.set(newPage);
    this.load();
  }

  open(report: AdminIssueReport): void {
    this.selected.set(report);
    this.noteDraft = report.admin_note ?? '';
  }

  close(): void {
    this.selected.set(null);
    this.noteDraft = '';
  }

  setStatus(status: string): void {
    const report = this.selected();
    if (!report || this.saving()) return;
    this.saving.set(true);
    this.adminApi.updateIssueReport(report.id, status, this.noteDraft.trim()).subscribe({
      next: () => {
        // Patch the row in place instead of refetching: the list may be
        // filtered by status, and a refetch would make the row the admin is
        // still looking at vanish out from under them.
        const note = this.noteDraft.trim() || report.admin_note;
        const updated = { ...report, status: status as AdminIssueReport['status'], admin_note: note };
        this.reports.update((rows) => rows.map((r) => (r.id === report.id ? updated : r)));
        this.selected.set(updated);
        this.saving.set(false);
      },
      error: () => {
        this.saving.set(false);
        this.error.set('Could not update that report.');
      },
    });
  }

  statusLabel(status: string): string {
    return this.statuses.find((s) => s.id === status)?.label ?? status;
  }

  contextEntries(report: AdminIssueReport): { key: string; value: string }[] {
    return Object.entries(report.context ?? {}).map(([key, value]) => ({ key, value }));
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

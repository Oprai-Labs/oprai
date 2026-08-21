import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { AdminApiService, AdminIssueReport, PaginatedResponse } from '../../services/admin-api.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';
import { TPipe } from '@core/i18n';

/** The three states a report moves through, in order. */
export const ISSUE_STEPS = [
  { id: 'open',        label: 'Submitted' },
  { id: 'in_progress', label: 'In review' },
  { id: 'resolved',    label: 'Resolved' },
] as const;

/**
 * The queue for Help → Report Issue.
 *
 * Two things happen here and both are visible to the person who filed the
 * report: moving it along the track, and answering it. The reply is not an
 * internal note — it renders on the reporter's own page — so the panel says
 * so where the box is, and saves it together with the status rather than as
 * a separate hidden step.
 */
@Component({
  selector: 'app-admin-issues',
  standalone: true,
  imports: [CommonModule,
    FormsModule,
    LucideAngularModule,
    TruncateAddressPipe,
    TimeAgoPipe,
    AdminLayoutComponent,
    SkeletonTableComponent, TPipe],
  templateUrl: './issues.component.html',
  styleUrl: './issues.component.scss',
})
export class IssuesComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly steps = ISSUE_STEPS;

  readonly reports = signal<AdminIssueReport[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly totalCount = signal(0);
  readonly page = signal(1);
  readonly pageSize = 25;

  readonly selected = signal<AdminIssueReport | null>(null);
  readonly saving = signal(false);
  readonly savedAt = signal<number | null>(null);

  /** Draft reply for the open report — the reporter sees this text. */
  noteDraft = '';
  statusFilter = '';
  categoryFilter = '';
  searchQuery = '';

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
    this.savedAt.set(null);
    this.error.set(null);
  }

  close(): void {
    this.selected.set(null);
    this.noteDraft = '';
    this.savedAt.set(null);
  }

  /** Save the reply against the report's current status, without moving it. */
  saveReply(): void {
    const report = this.selected();
    if (report) this.apply(report, report.status);
  }

  /** Move the report to a state, carrying whatever reply is in the box. */
  setStatus(status: string): void {
    const report = this.selected();
    if (report) this.apply(report, status);
  }

  private apply(report: AdminIssueReport, status: string): void {
    if (this.saving()) return;
    this.saving.set(true);
    this.error.set(null);

    this.adminApi.updateIssueReport(report.id, status, this.noteDraft.trim()).subscribe({
      next: () => {
        // Patch the row in place rather than refetching: the list may be
        // filtered by status, and a refetch would make the row the admin is
        // still reading vanish out from under them.
        const updated: AdminIssueReport = {
          ...report,
          status: status as AdminIssueReport['status'],
          admin_note: this.noteDraft.trim() || report.admin_note,
        };
        this.reports.update((rows) => rows.map((r) => (r.id === report.id ? updated : r)));
        this.selected.set(updated);
        this.saving.set(false);
        this.savedAt.set(Date.now());
      },
      error: () => {
        this.saving.set(false);
        this.error.set('Could not update that report.');
      },
    });
  }

  statusLabel(status: string): string {
    if (status === 'dismissed') return 'Closed';
    return this.steps.find((s) => s.id === status)?.label ?? status;
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

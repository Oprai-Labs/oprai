import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { ApiService } from '@core/services/api.service';

export type IssueCategory = 'bug' | 'feature' | 'account' | 'other';

export interface IssueReport {
  id: string;
  category: IssueCategory;
  subject: string;
  description: string;
  status: 'open' | 'in_progress' | 'resolved' | 'dismissed';
  adminNote: string | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * Help → Report Issue.
 *
 * The menu entry used to link to `github.com/anthropics/claude-code/issues` —
 * a completely unrelated project — so every report a user tried to file went
 * somewhere OPRAI would never read. This posts to OPRAI, and the reports land
 * in the admin queue.
 *
 * The page also lists what this wallet has already sent, with the status an
 * admin set. A report you cannot see the fate of feels like a report nobody
 * received, which is the fastest way to stop people sending them.
 */
@Component({
  selector: 'app-report-issue',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule],
  templateUrl: './report-issue.component.html',
  styleUrl: './report-issue.component.scss',
})
export class ReportIssueComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly categories: { id: IssueCategory; label: string; hint: string }[] = [
    { id: 'bug',     label: 'Something is broken', hint: 'An error, a wrong number, a screen that will not load' },
    { id: 'feature', label: 'Feature request',     hint: 'A protocol, an action or a view you want OPRAI to have' },
    { id: 'account', label: 'Wallet or account',   hint: 'Sign-in, balances, history or anything account-shaped' },
    { id: 'other',   label: 'Something else',      hint: "Anything that doesn't fit the boxes above" },
  ];

  category = signal<IssueCategory>('bug');
  subject = '';
  description = '';

  readonly submitting = signal(false);
  readonly submitted = signal(false);
  readonly error = signal<string | null>(null);

  readonly reports = signal<IssueReport[]>([]);
  readonly reportsLoading = signal(true);

  /** The description carries the report; a one-word body is not one. */
  readonly canSubmit = computed(() => !this.submitting() && this.description.trim().length >= 10);

  constructor() {
    this.loadReports();
  }

  private loadReports(): void {
    this.reportsLoading.set(true);
    this.api.get<{ reports: IssueReport[] }>('/chat/issues/mine').subscribe({
      next: (r) => {
        this.reports.set(r.reports ?? []);
        this.reportsLoading.set(false);
      },
      // A failure here is not worth an error banner: the form above still
      // works, and the list is context, not the task.
      error: () => this.reportsLoading.set(false),
    });
  }

  submit(): void {
    if (!this.canSubmit()) return;
    this.submitting.set(true);
    this.error.set(null);

    this.api.post<{ report: IssueReport }>('/chat/issues', {
      category: this.category(),
      subject: this.subject.trim(),
      description: this.description.trim(),
      // Captured so a report can be acted on without a follow-up round trip
      // asking the user where they were and what they were running.
      context: {
        route: this.router.url,
        userAgent: navigator.userAgent,
        viewport: `${window.innerWidth}x${window.innerHeight}`,
        language: navigator.language,
      },
    }).subscribe({
      next: (res) => {
        this.reports.update((rows) => [res.report, ...rows]);
        this.submitting.set(false);
        this.submitted.set(true);
        this.subject = '';
        this.description = '';
      },
      error: (err) => {
        this.submitting.set(false);
        this.error.set(
          err?.status === 429
            ? "You've sent several reports just now — please wait a few minutes before sending another."
            : 'Could not send your report. Please try again.',
        );
      },
    });
  }

  writeAnother(): void {
    this.submitted.set(false);
    this.error.set(null);
  }

  statusLabel(status: IssueReport['status']): string {
    switch (status) {
      case 'in_progress': return 'In progress';
      case 'resolved':    return 'Resolved';
      case 'dismissed':   return 'Closed';
      default:            return 'Open';
    }
  }

  categoryLabel(id: string): string {
    return this.categories.find((c) => c.id === id)?.label ?? 'Other';
  }
}

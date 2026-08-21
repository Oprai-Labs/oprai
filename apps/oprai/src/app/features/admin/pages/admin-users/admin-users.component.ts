import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminApiService, AdminAdminUser } from '../../services/admin-api.service';
import { TimeAgoPipe } from '@shared/pipes/time-ago.pipe';
import { LucideAngularModule } from 'lucide-angular';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonTableComponent } from '@shared/components/skeletons/skeleton-table.component';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-admin-users',
  standalone: true,
  imports: [CommonModule,
    FormsModule,
    TimeAgoPipe,
    LucideAngularModule,
    AdminLayoutComponent,
    SkeletonTableComponent, TPipe],
  templateUrl: './admin-users.component.html',
  styleUrl: './admin-users.component.scss',
})
export class AdminUsersComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly admins = signal<AdminAdminUser[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  // Create form
  readonly createOpen = signal(false);
  readonly creating = signal(false);
  readonly createError = signal<string | null>(null);
  readonly createdPassword = signal<string | null>(null);
  readonly createdAdmin = signal<AdminAdminUser | null>(null);
  newUsername = '';

  // Reset password
  readonly resetTarget = signal<AdminAdminUser | null>(null);
  readonly resetting = signal(false);
  readonly resetPassword = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminApi.getAdminUsers().subscribe({
      next: (res) => {
        this.admins.set(res.data);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Failed to load admin users.');
        this.loading.set(false);
      },
    });
  }

  openCreate(): void {
    this.newUsername = '';
    this.createError.set(null);
    this.createdPassword.set(null);
    this.createdAdmin.set(null);
    this.createOpen.set(true);
  }

  closeCreate(): void {
    this.createOpen.set(false);
    if (this.createdAdmin()) {
      this.load();
    }
  }

  submitCreate(): void {
    if (!this.newUsername.trim()) {
      this.createError.set('Username is required.');
      return;
    }
    this.creating.set(true);
    this.createError.set(null);
    this.adminApi.createAdminUser(this.newUsername.trim()).subscribe({
      next: (res) => {
        this.createdPassword.set(res.generatedPassword);
        this.createdAdmin.set(res.admin);
        this.creating.set(false);
      },
      error: (err: { error?: { message?: string } }) => {
        this.createError.set(err?.error?.message || 'Failed to create admin user.');
        this.creating.set(false);
      },
    });
  }

  confirmDelete(admin: AdminAdminUser): void {
    if (!confirm(`Delete admin "${admin.username}"? This cannot be undone.`)) return;
    this.adminApi.deleteAdminUser(admin.id).subscribe({
      next: () => this.load(),
      error: () => alert('Failed to delete admin user.'),
    });
  }

  openReset(admin: AdminAdminUser): void {
    this.resetTarget.set(admin);
    this.resetPassword.set(null);
  }

  closeReset(): void {
    this.resetTarget.set(null);
    this.resetPassword.set(null);
    this.resetting.set(false);
  }

  confirmReset(): void {
    const target = this.resetTarget();
    if (!target) return;
    this.resetting.set(true);
    this.adminApi.resetAdminPassword(target.id).subscribe({
      next: (res) => {
        this.resetPassword.set(res.generatedPassword);
        this.resetting.set(false);
      },
      error: () => {
        this.resetting.set(false);
        alert('Failed to reset password.');
      },
    });
  }

  copyToClipboard(text: string): void {
    navigator.clipboard.writeText(text).catch(() => {});
  }

  roleClass(role: string): string {
    return role === 'super_admin' ? 'badge--danger' : 'badge--info';
  }

  logout(): void {
    this.adminApi.adminLogout().subscribe({
      complete: () => { window.location.href = '/admin/login'; },
      error: () => { window.location.href = '/admin/login'; },
    });
  }
}

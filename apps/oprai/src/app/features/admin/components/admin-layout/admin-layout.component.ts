import { Component, Input, Output, EventEmitter } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { ThemeToggleComponent } from '@shared/components/theme-toggle/theme-toggle.component';

@Component({
  selector: 'app-admin-layout',
  standalone: true,
  imports: [RouterLink, LucideAngularModule, ThemeToggleComponent],
  template: `
    <div class="admin-layout">
      <aside class="admin-sidebar">
        <div class="admin-sidebar-header">
          <span class="admin-brand gradient-brand">OPRAI <span class="admin-label">Admin</span></span>
        </div>
        <nav class="admin-nav">
          <a routerLink="/admin" class="nav-item" [class.active]="activePage === 'dashboard'">
            <lucide-icon name="layout-dashboard" [size]="18" />
            Dashboard
          </a>
          <a routerLink="/admin/users" class="nav-item" [class.active]="activePage === 'users'">
            <lucide-icon name="users" [size]="18" />
            Users
          </a>
          <a routerLink="/admin/sessions" class="nav-item" [class.active]="activePage === 'sessions'">
            <lucide-icon name="message-square" [size]="18" />
            Sessions
          </a>
          <a routerLink="/admin/transactions" class="nav-item" [class.active]="activePage === 'transactions'">
            <lucide-icon name="activity" [size]="18" />
            Transactions
          </a>
          <a routerLink="/admin/audit-logs" class="nav-item" [class.active]="activePage === 'audit-logs'">
            <lucide-icon name="file-text" [size]="18" />
            Audit Logs
          </a>
          <a routerLink="/admin/analytics" class="nav-item" [class.active]="activePage === 'analytics'">
            <lucide-icon name="bar-chart-2" [size]="18" />
            Analytics
          </a>
          <a routerLink="/admin/ip-logs" class="nav-item" [class.active]="activePage === 'ip-logs'">
            <lucide-icon name="shield-alert" [size]="18" />
            IP Logs
          </a>
          <a routerLink="/admin/admin-users" class="nav-item" [class.active]="activePage === 'admin-users'">
            <lucide-icon name="user-cog" [size]="18" />
            Admin Users
          </a>
        </nav>
        <div class="admin-sidebar-footer">
          <app-theme-toggle></app-theme-toggle>
          <button class="btn-ghost" (click)="logoutClick.emit()">
            <lucide-icon name="log-out" [size]="18" />
            Logout
          </button>
        </div>
      </aside>
      <main class="admin-main">
        <ng-content></ng-content>
      </main>
    </div>
  `,
  styles: [`:host { display: block; height: 100%; }`],
})
export class AdminLayoutComponent {
  @Input({ required: true }) activePage!: string;
  @Output() logoutClick = new EventEmitter<void>();
}

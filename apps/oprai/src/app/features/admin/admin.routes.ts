import { Routes } from '@angular/router';
import { adminGuard } from '@core/guards/admin.guard';

export const ADMIN_ROUTES: Routes = [
  {
    path: 'login',
    loadComponent: () =>
      import('./pages/admin-login/admin-login.component').then(
        (m) => m.AdminLoginComponent
      ),
  },
  {
    path: '',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/dashboard/dashboard.component').then(
        (m) => m.DashboardComponent
      ),
  },
  {
    path: 'users',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/users/users.component').then(
        (m) => m.UsersComponent
      ),
  },
  {
    path: 'users/:wallet',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/users/users.component').then(
        (m) => m.UsersComponent
      ),
  },
  {
    path: 'transactions',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/transactions/transactions.component').then(
        (m) => m.TransactionsComponent
      ),
  },
  {
    path: 'sessions',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/sessions/sessions.component').then(
        (m) => m.SessionsComponent
      ),
  },
  {
    path: 'issues',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/issues/issues.component').then(
        (m) => m.IssuesComponent
      ),
  },
  {
    path: 'audit-logs',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/audit-logs/audit-logs.component').then(
        (m) => m.AuditLogsComponent
      ),
  },
  {
    path: 'ip-logs',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/ip-logs/ip-logs.component').then(
        (m) => m.IpLogsComponent
      ),
  },
  {
    path: 'analytics',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/analytics/analytics.component').then(
        (m) => m.AnalyticsComponent
      ),
  },
  {
    path: 'admin-users',
    canActivate: [adminGuard],
    loadComponent: () =>
      import('./pages/admin-users/admin-users.component').then(
        (m) => m.AdminUsersComponent
      ),
  },
];

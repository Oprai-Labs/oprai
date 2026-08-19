import { Routes } from '@angular/router';
import { MainLayoutComponent } from './layout/main-layout.component';
import { authGuard } from './core/guards/auth.guard';

export const routes: Routes = [
  {
    path: '',
    component: MainLayoutComponent,
    children: [
      // Chat
      {
        path: '',
        loadChildren: () =>
          import('./features/chat/chat.routes').then((m) => m.CHAT_ROUTES),
      },
      // Other pages
      {
        path: 'portfolio',
        canActivate: [authGuard],
        loadChildren: () =>
          import('./features/portfolio/portfolio.routes').then(
            (m) => m.PORTFOLIO_ROUTES
          ),
      },
      { path: 'agents', redirectTo: '', pathMatch: 'prefix' },
      { path: 'voice', redirectTo: '', pathMatch: 'prefix' },
      // Token Manager removed — legacy /burn URLs redirect home.
      { path: 'burn', redirectTo: '', pathMatch: 'prefix' },
      { path: 'stake', redirectTo: '', pathMatch: 'prefix' },
      // Help → Report Issue. Guarded because a report is attributed to the
      // wallet that filed it, and the page lists that wallet's own reports.
      {
        path: 'report-issue',
        canActivate: [authGuard],
        loadComponent: () =>
          import('./features/support/report-issue.component').then((m) => m.ReportIssueComponent),
      },
      // Manage published links. Guarded: it lists what THIS wallet shared.
      {
        path: 'shared-links',
        canActivate: [authGuard],
        loadComponent: () =>
          import('./features/share/shared-links.component').then((m) => m.SharedLinksComponent),
      },
      {
        path: 'settings',
        canActivate: [authGuard],
        loadComponent: () =>
          import('./features/settings/pages/settings-page/settings-page.component').then(
            (m) => m.SettingsPageComponent,
          ),
      },
      {
        path: 'rewards',
        canActivate: [authGuard],
        loadComponent: () =>
          import('./features/rewards/rewards.component').then((m) => m.RewardsComponent),
      },
      {
        path: 'wallets',
        canActivate: [authGuard],
        loadComponent: () =>
          import('./features/wallets/wallets.component').then((m) => m.WalletsComponent),
      },
      // Backward-compat redirects
      { path: 'market', redirectTo: '', pathMatch: 'full' },
      { path: 'explore', redirectTo: '', pathMatch: 'prefix' },
      { path: 'trade', redirectTo: '', pathMatch: 'full' },
      { path: 'projects', redirectTo: 'agents', pathMatch: 'prefix' },
      { path: 'defi', redirectTo: '', pathMatch: 'prefix' },
      { path: 'tokens', redirectTo: '', pathMatch: 'full' },
      { path: 'nft', redirectTo: 'portfolio', pathMatch: 'full' },
      { path: 'community', redirectTo: '', pathMatch: 'full' },
      { path: 'developer', redirectTo: '', pathMatch: 'full' },
      { path: 'ai', redirectTo: '', pathMatch: 'full' },
    ],
  },
  // Public shared chat. A sibling of the app shell, NOT a child: the layout
  // above renders the owner's sidebar, history and wallet button, and none of
  // that belongs around a conversation someone published for strangers. No
  // guard either — being readable without a wallet is the entire feature.
  {
    path: 'share/:token',
    loadComponent: () =>
      import('./features/share/shared-chat.component').then((m) => m.SharedChatComponent),
  },
  {
    path: 'admin',
    loadChildren: () =>
      import('./features/admin/admin.routes').then((m) => m.ADMIN_ROUTES),
  },
  {
    path: '**',
    redirectTo: '',
  },
];

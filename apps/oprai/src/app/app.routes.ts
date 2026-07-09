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
      {
        path: 'settings',
        canActivate: [authGuard],
        loadComponent: () =>
          import('./features/settings/pages/settings-page/settings-page.component').then(
            (m) => m.SettingsPageComponent,
          ),
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

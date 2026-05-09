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
      {
        path: 'agents',
        canActivate: [authGuard],
        loadChildren: () =>
          import('./features/projects/projects.routes').then(
            (m) => m.PROJECTS_ROUTES
          ),
      },
      {
        path: 'voice',
        canActivate: [authGuard],
        loadChildren: () =>
          import('./features/voice/voice.routes').then(
            (m) => m.VOICE_ROUTES
          ),
      },
      {
        path: 'burn',
        canActivate: [authGuard],
        loadChildren: () =>
          import('./features/burn/burn.routes').then(
            (m) => m.BURN_ROUTES
          ),
      },
      {
        path: 'stake',
        canActivate: [authGuard],
        loadChildren: () =>
          import('./features/stake/stake.routes').then(
            (m) => m.STAKE_ROUTES
          ),
      },
      {
        path: 'settings',
        redirectTo: '',
        pathMatch: 'full',
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

import { Routes } from '@angular/router';

export const BURN_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/burn-shell/burn-shell.component').then(
        (m) => m.BurnShellComponent
      ),
  },
];

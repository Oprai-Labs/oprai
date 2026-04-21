import { Routes } from '@angular/router';

export const PORTFOLIO_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/portfolio-shell/portfolio-shell.component').then(
        (m) => m.PortfolioShellComponent
      ),
  },
];

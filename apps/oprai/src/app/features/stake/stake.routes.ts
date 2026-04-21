import { Routes } from '@angular/router';

export const STAKE_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/native-stake-shell/native-stake-shell.component').then(
        (m) => m.NativeStakeShellComponent
      ),
  },
  {
    path: 'liquid',
    loadComponent: () =>
      import('./pages/liquid-stake-shell/liquid-stake-shell.component').then(
        (m) => m.LiquidStakeShellComponent
      ),
  },
];

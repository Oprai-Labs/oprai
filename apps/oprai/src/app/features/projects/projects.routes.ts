import { Routes } from '@angular/router';
import { ProjectsShellComponent } from './pages/projects-shell/projects-shell.component';

export const PROJECTS_ROUTES: Routes = [
  { path: '', component: ProjectsShellComponent },
  {
    path: 'yield-optimizer',
    loadComponent: () =>
      import('./pages/yield-optimizer/yield-optimizer.component').then(
        m => m.YieldOptimizerComponent
      ),
  },
];

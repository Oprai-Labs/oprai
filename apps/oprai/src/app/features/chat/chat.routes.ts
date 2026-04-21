import { Routes } from '@angular/router';

export const CHAT_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/chat-shell/chat-shell.component').then(
        (m) => m.ChatShellComponent
      ),
  },
  {
    path: 'c/:sessionId',
    loadComponent: () =>
      import('./pages/chat-shell/chat-shell.component').then(
        (m) => m.ChatShellComponent
      ),
  },
];

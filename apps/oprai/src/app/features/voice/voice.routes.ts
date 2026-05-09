import { Routes } from '@angular/router';

export const VOICE_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/voice-shell/voice-shell.component').then(
        (m) => m.VoiceShellComponent
      ),
  },
];

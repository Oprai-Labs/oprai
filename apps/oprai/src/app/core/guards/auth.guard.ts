import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from '../services/auth.service';

/**
 * Guard that redirects unauthenticated users to the home page.
 * Used for protecting chat routes that require wallet auth.
 */
export const authGuard: CanActivateFn = async () => {
  const authService = inject(AuthService);
  const router = inject(Router);

  // Wait for the initial cookie-based session restore before deciding — on a
  // deep-link / F5 to a protected route the router evaluates this guard before
  // AppComponent's restore finishes, so a synchronous isAuthenticated() check
  // was false and bounced the user to home.
  await authService.whenAuthReady();

  if (authService.isAuthenticated()) {
    return true;
  }

  return router.createUrlTree(['/']);
};

import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';
import { WalletService } from '../services/wallet.service';

/**
 * Global error interceptor.
 *
 * 401 Unauthorized:
 *   - Admin routes → clear admin token, redirect to /admin/login
 *   - User routes  → logout, then re-authenticate automatically if wallet
 *                    is still connected (e.g. expired JWT on page refresh)
 *
 * 5xx Server errors → log to console (toast can be wired here)
 */
export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const router = inject(Router);
  const authService = inject(AuthService);
  const walletService = inject(WalletService);

  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      if (error.status === 0) {
        console.error('[OPRAI] Server unreachable:', error.url);
      }

      if (error.status === 401) {
        const isAdminRoute = router.url.startsWith('/admin');

        if (isAdminRoute) {
          // Admin session is cookie-based (HttpOnly) — cannot be cleared from JS.
          // Redirect to login; the guard will re-validate on next navigation.
          router.navigate(['/admin/login']);
        } else {
          // Drop local state silently. Calling logout() here would POST /auth/logout
          // with the (already-rejected) token, which the gateway responds to by
          // adding the jti to its blocklist — creating a 401-loop self-DOS.
          authService.clearLocalAuth();

          // Re-authenticate silently if the wallet is still connected.
          // Guard against infinite loops: skip if the failing request was itself
          // an auth endpoint (/auth/nonce or /auth/verify returning 401 would
          // otherwise trigger another authenticate() → /auth/nonce → 401 → ...).
          const isAuthEndpoint =
            req.url.includes('/auth/nonce') ||
            req.url.includes('/auth/verify') ||
            req.url.includes('/auth/logout');

          if (!isAuthEndpoint && walletService.connected()) {
            authService.authenticate().subscribe({
              error: (authErr) =>
                console.warn('[Auth] Re-authentication after 401 failed:', authErr),
            });
          }
        }
      }

      if (error.status >= 500) {
        console.error(`[OPRAI] Server error ${error.status}:`, error.message);
      }

      return throwError(() => error);
    })
  );
};

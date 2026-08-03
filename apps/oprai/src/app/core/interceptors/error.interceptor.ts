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
          // Drop the (already-rejected) in-memory token/user silently. Calling
          // logout() here would POST /auth/logout with that token, which the
          // gateway blocklists — a 401-loop self-DOS. clearLocalAuth() no longer
          // wipes the sidebar session list, so a transient 401 can't blank it.
          authService.clearLocalAuth();

          // Re-authenticate silently if the wallet is still connected.
          // Guard against infinite loops: skip if the failing request was itself
          // an auth endpoint (/auth/nonce or /auth/verify returning 401 would
          // otherwise trigger another authenticate() → /auth/nonce → 401 → ...).
          const isAuthEndpoint =
            req.url.includes('/auth/nonce') ||
            req.url.includes('/auth/verify') ||
            req.url.includes('/auth/session') ||
            req.url.includes('/auth/logout');

          if (!isAuthEndpoint && walletService.connected()) {
            // Prefer a SILENT cookie-based recovery: the HttpOnly cookie usually
            // outlives the in-memory Bearer, so a stale-token 401 (e.g. the
            // Portfolio cost-basis fetch) can be healed via GET /auth/session
            // with NO wallet-signature popup and NO lost state. Only if the
            // cookie is also gone do we fall back to a fresh SIWS signature.
            // Silent recovery only. The fallback used to call authenticate(),
            // which opens a wallet signature prompt — so any background read
            // that happened to 401 asked the user to sign. Browsing the token
            // picker's tabs did exactly that: switching to Trending fetched
            // liquidity from a wallet-gated route, got a 401, and popped the
            // wallet. A signature is something a user asks for, never
            // something a failed background fetch demands of them. If the
            // cookie is gone too, the next deliberate action will re-auth.
            authService.restoreSession({ preserveSessionsOnFail: true }).then(() => {
              if (!authService.isAuthenticated()) {
                console.warn('[Auth] Session could not be restored silently; will re-auth on next action.');
              }
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

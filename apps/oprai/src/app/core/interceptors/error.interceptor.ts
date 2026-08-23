import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, from, switchMap, throwError } from 'rxjs';
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
          // Was there a live session before this 401? Capture it BEFORE clearing,
          // so an EVM (SIWE) session — which has no Solana adapter, so
          // walletService.connected() is false — is still recoverable. Without
          // this, a single transient 401 right after EVM login (a component's
          // early authed fetch racing the cookie) wiped the session with no
          // recovery, and the app fell back to "connect your wallet".
          const wasAuthenticated = authService.isAuthenticated();

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

          // One-shot flag: a request we already retried after a restore must not
          // loop if it 401s again (dead cookie) — surface it instead.
          const alreadyRetried = req.headers.has('X-Auth-Retry');

          if (!isAuthEndpoint && !alreadyRetried && (walletService.connected() || wasAuthenticated)) {
            // Prefer a SILENT cookie-based recovery: the HttpOnly cookie usually
            // outlives the in-memory Bearer, so a stale-token 401 (e.g. the
            // Portfolio cost-basis fetch) can be healed via GET /auth/session
            // with NO wallet-signature popup and NO lost state. Only if the
            // cookie is also gone do we fall back to a fresh SIWS signature.
            // Silent recovery only — never a wallet-signature popup from a failed
            // background fetch. If the cookie is gone too, the next deliberate
            // action will re-auth.
            //
            // AND retry the original request once after the restore: a stale
            // in-memory token used to fail the user's actual action (a perp
            // execute, a build) with "authentication required" even though the
            // cookie was still valid — they had to notice and click again, and
            // for a post-signature execute that meant re-signing. Healing then
            // replaying the same request makes the expiry invisible.
            return from(authService.restoreSession({ preserveSessionsOnFail: true })).pipe(
              switchMap(() => {
                if (!authService.isAuthenticated()) {
                  console.warn('[Auth] Session could not be restored silently; will re-auth on next action.');
                  return throwError(() => error);
                }
                // authInterceptor already ran (it precedes this one), so attach
                // the freshly-restored Bearer here; the cookie rides on
                // withCredentials. Mark it so a second 401 can't loop.
                const token = authService.getToken();
                const retried = req.clone({
                  withCredentials: true,
                  setHeaders: {
                    'X-Auth-Retry': '1',
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                  },
                });
                return next(retried);
              }),
            );
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

import { Injectable, inject, signal, computed } from '@angular/core';
import { Observable, from, throwError, Subject, share, firstValueFrom } from 'rxjs';
import { switchMap, tap, catchError, takeUntil } from 'rxjs/operators';
import bs58 from 'bs58';
import { ApiService } from './api.service';
import { WalletService } from './wallet.service';
import { SessionStorageService } from './session-storage.service';
import { isTokenExpired, getWalletFromToken } from '../utils/jwt';

export interface AuthUser {
  wallet: string;
  createdAt?: string;
}

interface NonceResponse {
  nonce: string;
  nonceId: string;
}

interface VerifyResponse {
  ok: boolean;
  token: string;
  expiresAt: string;
}

interface SessionResponse {
  authenticated: boolean;
  wallet?: string;
  expiresAt?: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);
  private readonly walletService = inject(WalletService);
  private readonly sessionStorage = inject(SessionStorageService);

  // Token is kept in memory only — NOT localStorage. The HttpOnly cookie set by
  // auth-service-go handles persistence and is sent automatically with every request.
  private readonly _token = signal<string | null>(null);
  private readonly _user = signal<AuthUser | null>(null);
  private readonly _authenticating = signal(false);

  /**
   * Fires on logout — cancels any in-flight authenticate() observable.
   * Prevents the sign result from setting a new JWT after the user logs out.
   */
  private readonly _cancel$ = new Subject<void>();

  /**
   * In-flight auth observable — shared among all concurrent callers.
   * Prevents multiple wallet sign requests when authenticate() is called
   * simultaneously (e.g. from AppComponent + WalletButtonComponent).
   */
  private _authInFlight$: Observable<AuthUser> | null = null;

  // Authenticated if we have a wallet in memory (set after login or restoreSession).
  // _token may be null after a page refresh (cookie-only mode); _user carries the identity.
  readonly isAuthenticated = computed(() => !!this._user());
  readonly user = this._user.asReadonly();
  readonly authenticating = this._authenticating.asReadonly();
  readonly token = this._token.asReadonly();

  /** Cancel an in-progress auth (resets UI state). */
  cancelAuth(): void {
    this._authenticating.set(false);
  }

  /**
   * Full SIWS authentication flow:
   * 1. Request nonce from auth-service
   * 2. Sign "OPRAI login: <nonce>" with wallet (gasless signMessage)
   * 3. POST signature to /auth/verify → get JWT
   *
   * Concurrent calls are deduplicated — only one wallet sign request is shown,
   * and all callers receive the same result via share().
   *
   * Cancelled immediately if logout() is called while signing is in progress.
   */
  authenticate(): Observable<AuthUser> {
    const walletAddress = this.walletService.publicKey();
    if (!walletAddress) {
      return throwError(() => new Error('No wallet connected'));
    }

    // Valid token already exists for this wallet — skip re-auth
    const existingToken = this._token();
    if (existingToken && this.isTokenValidForWallet(existingToken, walletAddress)) {
      const user: AuthUser = { wallet: walletAddress };
      this._user.set(user);
      return from(Promise.resolve(user));
    }

    // Deduplicate concurrent auth calls — return the same observable
    if (this._authInFlight$) {
      return this._authInFlight$;
    }

    // No need to clear stale sessions on wallet change — sessionStorage.setWallet()
    // (called in the success tap below) reloads the new wallet's namespace, and the
    // previous wallet's data stays safely on disk under its own key.

    this._authenticating.set(true);

    const auth$ = this.api
      .post<NonceResponse>('/auth/nonce', { wallet: walletAddress })
      .pipe(
        switchMap((nonceRes) => {
          const message = `OPRAI login: ${nonceRes.nonce}`;
          const messageBytes = new TextEncoder().encode(message);
          return from(this.walletService.signMessage(messageBytes)).pipe(
            switchMap((signatureBytes) => {
              const signature = bs58.encode(signatureBytes);
              return this.api.post<VerifyResponse>('/auth/verify', {
                walletAddress,
                signature,
                nonceId: nonceRes.nonceId,
              });
            })
          );
        }),
        tap((verifyRes) => {
          // Store token in memory only (HttpOnly cookie is set by the backend).
          this._token.set(verifyRes.token);
          this._user.set({ wallet: walletAddress });
          // Bind sidebar/local sessions to this wallet so cross-wallet titles can't leak.
          this.sessionStorage.setWallet(walletAddress);
          this._authenticating.set(false);
          this._authInFlight$ = null;
        }),
        switchMap(() => from(Promise.resolve({ wallet: walletAddress } as AuthUser))),
        catchError((err: unknown) => {
          this._authenticating.set(false);
          this._authInFlight$ = null;
          return throwError(() => err);
        }),
        // Cancel immediately if logout() is called while signing is in progress
        takeUntil(this._cancel$),
        // Multicast to all concurrent callers — one HTTP call, one wallet popup
        share()
      );

    this._authInFlight$ = auth$;
    return auth$;
  }

  /**
   * Restore session on app init by verifying the HttpOnly cookie with the backend.
   * The browser sends the cookie automatically (credentials: 'include' / withCredentials).
   * Returns true if a valid session was found, false otherwise.
   */
  /** Memoized promise of the FIRST cookie-based session restore. Route guards
   *  await this so a deep-link / F5 to a protected page (e.g. /portfolio) waits
   *  for the session to be known instead of bouncing to home while auth is
   *  still resolving. Restore runs exactly once. */
  private _authReady: Promise<void> | null = null;
  whenAuthReady(): Promise<void> {
    if (!this._authReady) this._authReady = this.restoreSession();
    return this._authReady;
  }

  /**
   * Restore the wallet session from the HttpOnly cookie.
   *
   * @param opts.preserveSessionsOnFail  When true (401-recovery from the error
   *   interceptor), a failed/negative restore does NOT clear the sidebar's
   *   session list. A portfolio page fires many requests; a single transient
   *   401 must not wipe the chat history and make the sidebar visibly reload.
   *   The initial app-load restore leaves this false so a genuinely
   *   unauthenticated boot still clears any stale wallet's sessions.
   */
  async restoreSession(opts?: { preserveSessionsOnFail?: boolean }): Promise<void> {
    try {
      const session = await firstValueFrom(
        this.api.get<SessionResponse>('/auth/session')
      );
      if (session.authenticated && session.wallet) {
        this._user.set({ wallet: session.wallet });
        this.sessionStorage.setWallet(session.wallet);
        // _token stays null — we use the cookie for requests; isAuthenticated() checks _user
      } else if (!opts?.preserveSessionsOnFail) {
        // Server says not authenticated — make sure no stale wallet's sessions stay visible.
        this.sessionStorage.setWallet(null);
      }
    } catch {
      // Network error or 4xx — session is not restored; user will need to re-auth.
      if (!opts?.preserveSessionsOnFail) {
        this.sessionStorage.setWallet(null);
      }
    }
  }

  logout(): void {
    // User-initiated logout: revokes the server-side token AND clears local state.
    // For automatic 401 recovery use clearLocalAuth() instead — calling
    // /auth/logout on every 401 revokes the user's own jti and creates a loop.
    this.api.post('/auth/logout', {}).subscribe({ error: () => {} });
    this.clearLocalAuth();
    // Genuine sign-out (and wallet change, which routes through logout()): drop
    // the in-memory session list so the previous wallet's conversations aren't
    // shown. Per-wallet on-disk storage is retained for the next sign-in.
    this.sessionStorage.setWallet(null);
  }

  /**
   * Clear local auth state without contacting the backend. Used when reacting
   * to a 401 (token already invalid server-side; calling /auth/logout would
   * just revoke it again and confuse the rate limiter).
   */
  clearLocalAuth(): void {
    // Cancel any in-flight authenticate() — prevents a sign result from
    // setting a new JWT after the user has disconnected.
    this._cancel$.next();
    this._authInFlight$ = null;
    this._authenticating.set(false);

    this._token.set(null);
    this._user.set(null);
    // Intentionally does NOT touch session-storage. This runs on TRANSIENT 401
    // recovery (error interceptor / re-opening a conversation) where the wallet
    // has NOT changed and the HttpOnly cookie is usually still valid. Wiping the
    // in-memory session list here made the sidebar flash "No conversations yet"
    // right after visiting a page that fires an authenticated call whose stale
    // in-memory Bearer 401s (e.g. Portfolio's cost-basis fetch), even though the
    // cookie could still restore the session. A genuine sign-out / wallet change
    // clears the list explicitly in logout().
  }

  getToken(): string | null {
    return this._token();
  }

  private isTokenValidForWallet(token: string, wallet: string): boolean {
    if (isTokenExpired(token)) return false;
    return getWalletFromToken(token) === wallet;
  }
}

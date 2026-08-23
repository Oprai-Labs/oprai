import { Injectable, inject, signal, computed } from '@angular/core';
import { Observable, from, throwError, Subject, share, firstValueFrom } from 'rxjs';
import { switchMap, tap, catchError, takeUntil } from 'rxjs/operators';
import bs58 from 'bs58';
import { ApiService } from './api.service';
import { WalletService } from './wallet.service';
import { SessionStorageService } from './session-storage.service';
import { isTokenExpired, getWalletFromToken } from '../utils/jwt';
import { setRpcAuthTokenProvider } from '../utils/solana-connection';

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

  // Let outbound RPC requests attach this JWT as a Bearer header so the /api/rpc
  // proxy can recognise a signed-in caller (see solana-connection.ts). Reads the
  // signal live on each call, so it always reflects the current token — or null
  // after logout / in cookie-only mode, where the proxy's Origin check covers us.
  //
  // Crucially, an EXPIRED token is returned as null, not attached: the gateway's
  // JWTAuth rejects an expired Bearer with 401 and does NOT fall through to the
  // cookie, so sending a stale token would break RPC for a user whose cookie is
  // still perfectly valid. Withholding it lets the cookie/Origin path carry them.
  private readonly _rpcTokenBinding = (() => {
    setRpcAuthTokenProvider(() => {
      const t = this._token();
      return t && !isTokenExpired(t) ? t : null;
    });
    return true;
  })();
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

  /**
   * Build the domain-bound sign-in message (EIP-4361 / SIWS style). The wallet
   * shows the user WHICH SITE and WHICH ACCOUNT they're authorizing, so a
   * signature phished on another origin is visible for what it is — the bare
   * "OPRAI login: <nonce>" it replaces showed neither. auth-service verifies the
   * signature over these EXACT bytes and checks the nonce + domain, so this
   * format must stay byte-for-byte in lockstep with the backend.
   */
  private buildSignInMessage(address: string, chainLabel: string, chainId: string, nonce: string): string {
    const host = typeof window !== 'undefined' ? window.location.host : 'app.oprai.xyz';
    const origin = typeof window !== 'undefined' ? window.location.origin : 'https://app.oprai.xyz';
    const issuedAt = new Date().toISOString();
    return [
      `${host} wants you to sign in with your ${chainLabel} account:`,
      address,
      '',
      'Sign in to OPRAI. This request will not trigger a blockchain transaction or cost any gas.',
      '',
      `URI: ${origin}`,
      'Version: 1',
      `Chain ID: ${chainId}`,
      `Nonce: ${nonce}`,
      `Issued At: ${issuedAt}`,
    ].join('\n');
  }

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
          const message = this.buildSignInMessage(walletAddress, 'Solana', 'mainnet', nonceRes.nonce);
          const messageBytes = new TextEncoder().encode(message);
          return from(this.signWithDeadline(messageBytes)).pipe(
            switchMap((signatureBytes) => {
              const signature = bs58.encode(signatureBytes);
              return this.api.post<VerifyResponse>('/auth/verify', {
                walletAddress,
                signature,
                nonceId: nonceRes.nonceId,
                message,
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
   * Sign in with an EVM wallet (SIWE). Mirrors authenticate() but verifies an
   * EIP-191 personal_sign over "OPRAI login: <nonce>" with chain:'ethereum'. The
   * account is resolved/created EVM-primary server-side. The session lives in the
   * HttpOnly cookie; we set the in-memory user to the EVM address.
   */
  async authenticateEvm(
    provider: { request(a: { method: string; params?: unknown[] }): Promise<unknown> },
    address: string,
  ): Promise<AuthUser> {
    const addr = address.toLowerCase();
    this._authenticating.set(true);
    try {
      const nonceRes = await firstValueFrom(this.api.post<NonceResponse>('/auth/nonce', { wallet: addr }));
      const message = this.buildSignInMessage(address, 'Ethereum', '1', nonceRes.nonce);
      const signature = (await provider.request({ method: 'personal_sign', params: [message, address] })) as string;
      const verifyRes = await firstValueFrom(
        this.api.post<VerifyResponse>('/auth/verify', {
          walletAddress: addr,
          signature,
          nonceId: nonceRes.nonceId,
          chain: 'ethereum',
          message,
        }),
      );
      if (verifyRes.token) this._token.set(verifyRes.token);
      const user: AuthUser = { wallet: addr };
      this._user.set(user);
      this.sessionStorage.setWallet(addr);
      return user;
    } finally {
      this._authenticating.set(false);
    }
  }

  /**
   * Ask the wallet to sign, and give up if it never answers.
   *
   * A wallet popup that is dismissed, blocked, or simply never opened leaves
   * `signMessage` pending forever. Nothing downstream can settle: the auth
   * observable never completes, `authenticating` stays true, and the chat sits
   * on a loading skeleton with no error, no prompt and no way out — which is
   * exactly what "I connected my wallet and it just sits there" looks like.
   *
   * Two minutes is long enough to find the window behind a browser and short
   * enough that a stuck flow becomes a message with a retry.
   */
  private async signWithDeadline(message: Uint8Array): Promise<Uint8Array> {
    const TIMEOUT_MS = 120_000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(
          'Your wallet never returned a signature. Open it, approve the sign-in request, and try again.',
        )),
        TIMEOUT_MS,
      );
    });
    try {
      return await Promise.race([this.walletService.signMessage(message), deadline]);
    } finally {
      clearTimeout(timer);
    }
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

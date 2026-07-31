import { Component, OnInit, OnDestroy, inject, effect, signal, untracked } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { Subscription } from 'rxjs';
import { ThemeService } from './core/services/theme.service';
import { WalletService } from './core/services/wallet.service';
import { AuthService } from './core/services/auth.service';
import { AppVersionService } from './core/services/app-version.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  templateUrl: './app.component.html',
})
export class AppComponent implements OnInit, OnDestroy {
  private readonly themeService = inject(ThemeService);
  private readonly walletService = inject(WalletService);
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  private readonly appVersion = inject(AppVersionService);

  private accountChangedSub?: Subscription;

  // Set true after the initial cookie-restore + auto-connect finish, so the
  // auto-auth effect below doesn't fire a signature prompt before the silent
  // cookie session had its chance.
  private readonly _bootDone = signal(false);
  // The wallet key we last kicked off an auto-authenticate for — prevents a
  // rejected signature from looping into another prompt. Reset on disconnect
  // (so a fresh reconnect re-attempts) and on successful auth.
  private _lastAutoAuthKey: string | null = null;

  constructor() {
    // Single source of truth for "wallet connected but no session → sign in".
    // Covers EVERY path that leaves us connected-but-unauthenticated: the
    // connect button, a wallet account-switch, a page where a prior
    // authenticate() was still in-flight, etc. Without this, reconnecting a
    // wallet left the sidebar on "No conversations yet" until the user manually
    // refreshed (which re-triggered auth) — the flow the user flagged.
    effect(() => {
      const connected = this.walletService.connected();
      const key = this.walletService.publicKey();
      const authed = this.authService.isAuthenticated();
      const authenticating = this.authService.authenticating();

      if (authed) { this._lastAutoAuthKey = null; return; }
      if (!connected || !key) { this._lastAutoAuthKey = null; return; }
      if (!this._bootDone()) return;          // let the cookie restore go first
      if (authenticating) return;              // a sign request is already open
      if (this._lastAutoAuthKey === key) return; // already tried this key; don't loop

      this._lastAutoAuthKey = key;
      untracked(() =>
        this.authService.authenticate().subscribe({
          error: (err) => console.warn('[Auth] auto sign-in on connect failed:', err),
        }),
      );
    });
  }

  async ngOnInit(): Promise<void> {
    this.themeService.initialize();
    // Silently pick up a newer build while the tab is in the background, so a
    // shipped fix stops looking unshipped. No banner — see the service.
    this.appVersion.start();

    // Restore session from HttpOnly cookie via GET /auth/session. Uses the
    // memoized whenAuthReady() so route guards awaiting the same restore share
    // this single call instead of firing a duplicate.
    await this.authService.whenAuthReady();

    // Auto-reconnect wallet if previously trusted (silent, no popup).
    // 3s timeout prevents wallet adapters from hanging on unfamiliar domains (e.g. tunnels).
    await Promise.race([
      this.walletService.autoConnect(),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 3000)),
    ]);

    // Cookie restore + auto-connect are done — release the auto-auth effect. If
    // the cookie session didn't authenticate but the wallet is connected, the
    // effect now signs in (and loads history) without a manual refresh.
    this._bootDone.set(true);

    // Re-authenticate when user switches accounts in their wallet.
    // SECURITY: when this fires with a non-null newKey we MUST logout (drops
    // the previous wallet's JWT and clears sessionStorage namespace) BEFORE
    // calling authenticate(). Without that, the cached JWT would skip the
    // re-auth path (`isTokenValidForWallet` may still match if the new
    // wallet hadn't roundtripped yet) and chat history fetches would still
    // attribute to the previous wallet.
    //
    // When fired with null (pure disconnect), we just logout — there's no
    // wallet to re-authenticate to. The next connect will re-fire with the
    // new key.
    this.accountChangedSub = this.walletService.accountChanged$.subscribe((newKey) => {
      // `accountChanged$` fires on EVERY successful connect — including benign
      // re-announcements of the SAME wallet (silent auto-reconnect, tab focus,
      // extension re-inject). Tearing down on those would logout() → REVOKE a
      // perfectly valid JWT (jti blocklisted server-side) and force a fresh
      // SIWS signature; worse, any in-flight RPC/build/simulate call races the
      // revoke and 401s ("token_revoked"). So only act when the account
      // ACTUALLY changed — a same-wallet reconnect keeps the live session.
      const currentWallet = this.authService.user()?.wallet ?? null;
      if (newKey && newKey === currentWallet && this.authService.isAuthenticated()) {
        return;
      }

      this.authService.logout();
      // SECURITY: navigate away from any wallet-scoped page (specifically the
      // current chat session URL like `/c/<sessionId>`) to drop any in-memory
      // message state attributed to the previous wallet. Without this, the
      // chat-shell on the active route keeps rendering the old wallet's
      // messages until the user manually opens a different conversation.
      // Going to `/` (chat home) gives a clean slate aligned with the new
      // wallet's session list.
      void this.router.navigateByUrl('/');
      // Re-authentication for the new wallet is handled by the auto-auth effect
      // (connected + !authed → sign in). Doing it here too raced the effect and
      // the in-flight-dedup guard, which is how a reconnect could end up
      // unauthenticated until a manual refresh.
    });
  }

  ngOnDestroy(): void {
    this.accountChangedSub?.unsubscribe();
  }
}

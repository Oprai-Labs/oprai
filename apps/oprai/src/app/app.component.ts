import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { Subscription } from 'rxjs';
import { ThemeService } from './core/services/theme.service';
import { WalletService } from './core/services/wallet.service';
import { AuthService } from './core/services/auth.service';

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

  private accountChangedSub?: Subscription;

  async ngOnInit(): Promise<void> {
    this.themeService.initialize();

    // Restore session from HttpOnly cookie via GET /auth/session
    await this.authService.restoreSession();

    // Auto-reconnect wallet if previously trusted (silent, no popup).
    // 3s timeout prevents wallet adapters from hanging on unfamiliar domains (e.g. tunnels).
    const autoConnected = await Promise.race([
      this.walletService.autoConnect(),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 3000)),
    ]);

    // If wallet reconnected but JWT expired, trigger re-authentication
    if (autoConnected && !this.authService.isAuthenticated()) {
      this.authService.authenticate().subscribe({
        error: (err) => {
          console.warn('[Auth] Auto-authentication failed:', err);
          this.authService.logout();
        },
      });
    }

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
      if (!newKey) return; // pure disconnect — nothing to authenticate against
      this.authService.authenticate().subscribe({
        error: (err) => console.warn('[Auth] Account-change re-auth failed:', err),
      });
    });
  }

  ngOnDestroy(): void {
    this.accountChangedSub?.unsubscribe();
  }
}

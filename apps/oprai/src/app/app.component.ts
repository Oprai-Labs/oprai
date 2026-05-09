import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
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

    // Re-authenticate when user switches accounts in their wallet
    this.accountChangedSub = this.walletService.accountChanged$.subscribe(() => {
      this.authService.logout();
      this.authService.authenticate().subscribe({
        error: (err) => console.warn('[Auth] Account-change re-auth failed:', err),
      });
    });
  }

  ngOnDestroy(): void {
    this.accountChangedSub?.unsubscribe();
  }
}

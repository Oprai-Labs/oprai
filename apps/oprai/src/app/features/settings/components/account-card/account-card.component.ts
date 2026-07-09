import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { AuthService } from '@core/services/auth.service';
import { WalletService } from '@core/services/wallet.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';

@Component({
  selector: 'app-account-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, TruncateAddressPipe],
  templateUrl: './account-card.component.html',
  styleUrl: './account-card.component.scss',
})
export class AccountCardComponent {
  private readonly auth = inject(AuthService);
  private readonly wallet = inject(WalletService);
  private readonly router = inject(Router);

  readonly user = this.auth.user;
  readonly walletName = this.wallet.walletName;
  readonly copied = signal(false);

  async copyAddress(): Promise<void> {
    const addr = this.user()?.wallet;
    if (!addr) return;
    try {
      await navigator.clipboard.writeText(addr);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 1500);
    } catch {
      // Clipboard unavailable — silently no-op
    }
  }

  async disconnect(): Promise<void> {
    try {
      await this.wallet.disconnect();
    } catch {
      // Wallet adapter may already be disconnected — fall through to logout
    }
    this.auth.logout();
    void this.router.navigate(['/']);
  }
}

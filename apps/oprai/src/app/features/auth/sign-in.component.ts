import { ChangeDetectionStrategy, Component, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WalletButtonComponent } from '@shared/components/wallet-button/wallet-button.component';
import { AuthService } from '@core/services/auth.service';
import { TPipe } from '@core/i18n';

/**
 * Full-screen sign-in gate shown before the app when no wallet session exists.
 * Layout: OPRAI logo top-left, Sign in / Sign up top-right, a centred brand +
 * wallet-connect CTA. Every entry point opens the same wallet modal — with
 * wallet auth there's no separate registration, so "Sign up" and "Sign in" both
 * connect (a first-time wallet is a sign-up, a returning one a sign-in).
 */
@Component({
  selector: 'app-sign-in',
  standalone: true,
  imports: [CommonModule, WalletButtonComponent, TPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="signin">
      <header class="signin-topbar">
        <img class="signin-logo" src="oprai_fullname_icon.svg" [attr.alt]="'OPRAI' | t" height="26" />
        <div class="signin-actions">
          <button type="button" class="signin-btn signin-btn--ghost" (click)="connect()">{{ 'Sign in' | t }}</button>
          <button type="button" class="signin-btn signin-btn--primary" (click)="connect()">{{ 'Sign up' | t }}</button>
        </div>
      </header>

      <main class="signin-hero">
        <img class="signin-mark" src="oprai_main_icon.svg" [attr.alt]="'OPRAI' | t" />
        <h1 class="signin-title">{{ 'Your AI copilot for on-chain DeFi' | t }}</h1>
        <p class="signin-sub">{{ 'Chat to swap, lend, borrow, trade perps and more — across Robinhood Chain, EVM and Solana.' | t }}</p>
        <div class="signin-cta">
          <app-wallet-button #walletBtn />
        </div>
        <p class="signin-fine">{{ 'Wallet-based sign-in — no email, no password. Your keys never leave your wallet.' | t }}</p>
      </main>
    </div>
  `,
  styles: [`
    :host { display:block; }
    .signin {
      min-height:100vh; min-height:100dvh; display:flex; flex-direction:column;
      background:var(--op-bg-surface-0, var(--op-bg, #0c0c10)); color:var(--op-text-primary);
    }
    .signin-topbar {
      display:flex; align-items:center; justify-content:space-between;
      padding:18px 24px; gap:16px;
    }
    .signin-logo { height:26px; width:auto; display:block; }
    .signin-actions { display:flex; align-items:center; gap:10px; }
    .signin-btn {
      font-family:var(--op-font-display, inherit); font-size:.9rem; font-weight:600;
      padding:9px 18px; border-radius:999px; cursor:pointer; transition:all .12s; border:1px solid transparent;
    }
    .signin-btn--ghost {
      background:transparent; color:var(--op-text-secondary);
      border-color:var(--op-border, rgba(125,125,150,.28));
    }
    .signin-btn--ghost:hover { color:var(--op-text-primary); border-color:var(--op-text-secondary); }
    .signin-btn--primary {
      color:#fff; border:0;
      background:linear-gradient(90deg, var(--op-brand, #5b5fc7), var(--op-brand-2, #06b6d4));
    }
    .signin-btn--primary:hover { filter:brightness(1.06); }

    .signin-hero {
      flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center;
      text-align:center; padding:32px 24px 96px; gap:18px;
    }
    .signin-mark { width:72px; height:72px; margin-bottom:6px; }
    .signin-title {
      font-family:var(--op-font-display, inherit); font-size:clamp(1.6rem, 4vw, 2.6rem);
      font-weight:800; letter-spacing:-.02em; max-width:16ch; text-wrap:balance; margin:0;
      color:var(--op-text-primary);
    }
    .signin-sub {
      font-size:1rem; color:var(--op-text-secondary); max-width:52ch; line-height:1.55; margin:0;
    }
    .signin-cta { margin-top:10px; }
    .signin-fine { font-size:.78rem; color:var(--op-text-tertiary, var(--op-text-secondary)); opacity:.85; margin:0; }
  `],
})
export class SignInComponent {
  readonly authService = inject(AuthService);
  @ViewChild('walletBtn') private walletBtn?: WalletButtonComponent;

  /** Every sign-in / sign-up entry point opens the wallet modal. */
  connect(): void {
    this.walletBtn?.openModal();
  }
}

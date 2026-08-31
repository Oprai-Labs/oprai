import { ChangeDetectionStrategy, Component, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WalletButtonComponent } from '@shared/components/wallet-button/wallet-button.component';
import { AuthService } from '@core/services/auth.service';
import { TPipe } from '@core/i18n';

/**
 * Full-screen sign-in / sign-up gate shown before the app when no wallet session
 * exists. Two-panel, committed-dark design (explicit colours, not theme tokens —
 * a dark bg with light-theme text was unreadable): OPRAI wordmark top-left, a
 * brand hero on the left, and a Welcome + wallet-connect panel on the right.
 * With wallet auth there's no separate registration, so every entry point opens
 * the same wallet modal (a first-time wallet is a sign-up, a returning one a
 * sign-in). Monochrome logos are whitened with a CSS filter (the SVGs are
 * currentColor/black, invisible on the dark panel otherwise).
 */
@Component({
  selector: 'app-sign-in',
  standalone: true,
  imports: [CommonModule, WalletButtonComponent, TPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="signin">
      <img class="signin-wordmark" src="oprai_fullname_icon.svg" [attr.alt]="'OPRAI' | t" />

      <!-- Left: brand hero -->
      <section class="signin-hero">
        <div class="signin-rings" aria-hidden="true"></div>
        <div class="signin-glow" aria-hidden="true"></div>
        <div class="signin-mark-wrap">
          <img class="signin-mark" src="oprai_main_icon.svg" alt="" />
        </div>
        <div class="signin-hero-copy">
          <span class="signin-badge">{{ 'OPRAI · AI DeFi Copilot' | t }}</span>
          <h2 class="signin-hero-title">{{ 'Chat your way through on-chain finance' | t }}</h2>
          <p class="signin-hero-text">{{ 'Swap, lend, borrow, launch tokens and trade perps across Robinhood Chain, EVM and Solana — just by asking.' | t }}</p>
        </div>
      </section>

      <!-- Right: welcome + connect -->
      <section class="signin-panel">
        <div class="signin-card">
          <img class="signin-card-mark" src="oprai_main_icon.svg" alt="" />
          <h1 class="signin-welcome">{{ 'Welcome' | t }}</h1>
          <p class="signin-welcome-sub">{{ 'Connect your wallet to sign in and get started.' | t }}</p>

          <div class="signin-connect">
            <app-wallet-button #walletBtn />
          </div>

          <div class="signin-alt">
            <button type="button" class="signin-ghost" (click)="connect()">{{ 'Sign in' | t }}</button>
            <span class="signin-alt-sep">·</span>
            <button type="button" class="signin-ghost" (click)="connect()">{{ 'Sign up' | t }}</button>
          </div>

          <p class="signin-fine">{{ 'Wallet-based sign-in — no email, no password. Your keys never leave your wallet.' | t }}</p>
        </div>
      </section>
    </div>
  `,
  styles: [`
    :host { display:block; }
    .signin {
      position:relative; display:flex; min-height:100vh; min-height:100dvh;
      background:#0a0b0f; color:#f3f4f8;
      font-family:var(--op-font-body, ui-sans-serif, system-ui, sans-serif);
      overflow:hidden;
    }
    .signin-wordmark {
      position:absolute; top:24px; left:28px; height:24px; width:auto; z-index:4;
      filter:brightness(0) invert(1); opacity:.95;
    }

    /* ── Left hero ── */
    .signin-hero {
      position:relative; flex:1.1; display:flex; align-items:center; justify-content:center;
      border-right:1px solid rgba(255,255,255,.06); overflow:hidden; min-width:0;
    }
    .signin-rings {
      position:absolute; inset:-20%; z-index:0;
      background:repeating-radial-gradient(circle at 50% 46%,
        transparent 0 78px, rgba(255,255,255,.045) 78px 79px);
      -webkit-mask-image:radial-gradient(circle at 50% 46%, #000 30%, transparent 72%);
      mask-image:radial-gradient(circle at 50% 46%, #000 30%, transparent 72%);
    }
    .signin-glow {
      position:absolute; z-index:0; width:520px; height:520px; left:50%; top:46%;
      transform:translate(-50%,-50%); border-radius:50%; filter:blur(80px); opacity:.28;
      background:radial-gradient(circle, #5b5fc7 0%, #06b6d4 55%, transparent 72%);
    }
    .signin-mark-wrap {
      position:relative; z-index:1; display:grid; place-items:center;
      width:150px; height:150px; border-radius:50%;
      background:radial-gradient(circle at 50% 40%, rgba(91,95,199,.22), transparent 70%);
    }
    .signin-mark {
      width:88px; height:88px; filter:brightness(0) invert(1)
        drop-shadow(0 0 26px rgba(91,95,199,.55));
    }
    .signin-hero-copy {
      position:absolute; left:44px; right:44px; bottom:44px; z-index:1; max-width:520px;
    }
    .signin-badge {
      display:inline-block; font-size:.72rem; font-weight:600; letter-spacing:.04em;
      text-transform:uppercase; color:#c8cbe8;
      padding:5px 11px; border-radius:999px;
      border:1px solid rgba(255,255,255,.14); background:rgba(255,255,255,.04);
    }
    .signin-hero-title {
      margin:16px 0 10px; font-family:var(--op-font-display, inherit);
      font-size:clamp(1.4rem, 2.4vw, 2rem); font-weight:800; letter-spacing:-.02em;
      line-height:1.15; color:#fff; text-wrap:balance;
    }
    .signin-hero-text { margin:0; font-size:.98rem; line-height:1.6; color:#9aa0ad; max-width:44ch; }

    /* ── Right panel ── */
    .signin-panel { flex:1; display:grid; place-items:center; padding:32px 24px; min-width:0; }
    .signin-card { width:100%; max-width:360px; text-align:center; }
    .signin-card-mark { display:none; width:56px; height:56px; margin:0 auto 18px; filter:brightness(0) invert(1); }
    .signin-welcome {
      margin:0 0 8px; font-family:var(--op-font-display, inherit);
      font-size:2.1rem; font-weight:800; letter-spacing:-.02em; color:#fff;
    }
    .signin-welcome-sub { margin:0 0 26px; font-size:.95rem; color:#9aa0ad; line-height:1.5; }
    .signin-connect { display:flex; justify-content:center; }
    .signin-alt { display:flex; align-items:center; justify-content:center; gap:8px; margin-top:16px; }
    .signin-alt-sep { color:#565a66; }
    .signin-ghost {
      background:none; border:0; padding:2px 4px; cursor:pointer;
      font-size:.86rem; font-weight:600; color:#9aa0ad; transition:color .12s;
    }
    .signin-ghost:hover { color:#fff; }
    .signin-fine { margin:26px auto 0; max-width:34ch; font-size:.74rem; line-height:1.5; color:#6a6f7b; }

    @media (max-width:880px) {
      .signin-hero { display:none; }
      .signin-panel { flex:1; }
      .signin-card-mark { display:block; }
    }
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

import {
  Component, NgZone, OnDestroy, OnInit, computed, inject, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { firstValueFrom } from 'rxjs';
import bs58 from 'bs58';
import { AccountService, LinkedIdentity } from '../../core/services/account.service';
import { WalletService } from '../../core/services/wallet.service';
import { BrandIconComponent } from './brand-icon.component';

const TYPE_META: Record<string, { label: string; color: string; tint: string }> = {
  solana_wallet: { label: 'Solana wallet', color: '#9945FF', tint: 'rgba(153,69,255,.12)' },
  evm_wallet: { label: 'Ethereum wallet', color: '#627eea', tint: 'rgba(98,126,234,.12)' },
  telegram: { label: 'Telegram', color: '#229ED9', tint: 'rgba(34,158,217,.12)' },
  twitter: { label: 'X (Twitter)', color: '#1d1d1f', tint: 'rgba(120,120,130,.14)' },
  email: { label: 'Email', color: '#5b5fc7', tint: 'rgba(91,95,199,.12)' },
};

// Numeric bot id of @Oprai_Labs_Bot (from getMe) — used to open Telegram's
// OAuth popup. The bot's login domain must be app.oprai.xyz (set in @BotFather).
const TELEGRAM_BOT_ID = '8820421943';

interface EthProvider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
}
interface EvmWallet {
  uuid: string;
  name: string;
  icon: string;
  rdns?: string;
  provider: EthProvider;
  detected: boolean;
  url?: string;
}

// The biggest EVM wallets — always shown under "More wallets" (minus any already
// detected), so users see the full list, not only what's installed. Icons come
// from DuckDuckGo's favicon service (reliable, CSP allows https images) with a
// fall back to the generic EVM mark if one fails to load.
const ICO = (domain: string) => `https://icons.duckduckgo.com/ip3/${domain}.ico`;
const INSTALLABLE_EVM: { name: string; url: string; icon: string }[] = [
  { name: 'MetaMask', url: 'https://metamask.io/download/', icon: ICO('metamask.io') },
  { name: 'Rabby Wallet', url: 'https://rabby.io/', icon: ICO('rabby.io') },
  { name: 'Coinbase Wallet', url: 'https://www.coinbase.com/wallet/downloads', icon: ICO('coinbase.com') },
  { name: 'Trust Wallet', url: 'https://trustwallet.com/download', icon: ICO('trustwallet.com') },
  { name: 'Rainbow', url: 'https://rainbow.me/', icon: ICO('rainbow.me') },
  { name: 'OKX Wallet', url: 'https://www.okx.com/web3', icon: ICO('okx.com') },
  { name: 'Zerion', url: 'https://zerion.io/download', icon: ICO('zerion.io') },
  { name: 'Phantom', url: 'https://phantom.app/download', icon: ICO('phantom.app') },
  { name: 'Uniswap Wallet', url: 'https://wallet.uniswap.org/', icon: ICO('uniswap.org') },
  { name: 'Brave Wallet', url: 'https://brave.com/wallet/', icon: ICO('brave.com') },
];


@Component({
  selector: 'app-wallets',
  standalone: true,
  imports: [CommonModule, LucideAngularModule, BrandIconComponent],
  template: `
    <div class="wl">
      <header class="wl-head">
        <h1><lucide-icon name="wallet" [size]="20" /> Wallets</h1>
        <p>One OPRAI account, every wallet and social. Balances, rewards and history roll up across all of them.</p>
      </header>

      @if (loading()) {
        <div class="wl-skel"><div class="sk"></div><div class="sk"></div></div>
      } @else if (error()) {
        <div class="wl-error"><lucide-icon name="triangle-alert" [size]="16" /> {{ error() }} <button (click)="load()">Retry</button></div>
      } @else {
        <!-- ── Linked ─────────────────────────────────────────── -->
        <section class="wl-card">
          <div class="wl-card-top">
            <span class="wl-lbl">Linked</span>
            <span class="wl-count">{{ identities().length }}</span>
          </div>

          <div class="wl-list">
            @for (id of identities(); track id.id) {
              <div class="wl-row">
                <div class="wl-tile" [style.background]="meta(id).tint">
                  <app-brand-icon [type]="id.type" [size]="22" />
                </div>
                <div class="wl-main">
                  <div class="wl-name">
                    {{ meta(id).label }}
                    @if (id.isPrimary) { <span class="wl-primary">Primary</span> }
                  </div>
                  <button class="wl-addr" (click)="copy(id.identifier)" [title]="'Copy ' + id.identifier">
                    {{ display(id) }}
                    <lucide-icon [name]="copied() === id.identifier ? 'check' : 'copy'" [size]="12" />
                  </button>
                </div>
                <div class="wl-actions">
                  @if (!id.isPrimary) {
                    @if (id.type === 'solana_wallet' || id.type === 'evm_wallet') {
                      <button class="wl-iconbtn wl-star" (click)="makePrimary(id)" [disabled]="busy()" title="Make primary">
                        <lucide-icon name="shield-check" [size]="15" />
                      </button>
                      <span class="wl-primary-lock" title="Wallets are permanently linked — can't be unlinked"><lucide-icon name="lock" [size]="15" /></span>
                    } @else {
                      <button class="wl-iconbtn wl-unlink" (click)="unlink(id)" [disabled]="busy()" title="Disconnect">
                        <lucide-icon name="trash-2" [size]="15" />
                      </button>
                    }
                  } @else {
                    <span class="wl-primary-lock" title="Your primary login wallet"><lucide-icon name="badge-check" [size]="18" /></span>
                  }
                </div>
              </div>
            }
          </div>
        </section>

        <!-- ── Link-a-Solana-wallet flow (in place) ───────────── -->
        @if (linkActive()) {
          <section class="wl-card wl-flowcard">
            <div class="wl-linkflow-head"><app-brand-icon type="solana_wallet" [size]="18" /> Link another Solana wallet</div>
            <ol class="wl-steps">
              <li>Open your wallet extension and <b>switch to the account you want to add</b>.</li>
              <li>Come back and sign — this proves you own it. Your session stays on your primary wallet.</li>
              <li><b>Permanent:</b> once linked, this wallet is bound to your account and can't be unlinked.</li>
            </ol>
            <div class="wl-connected-now">
              Currently selected: <span>{{ shortId(wallet.publicKey() || '—') }}</span>
              @if ((wallet.publicKey() || '') === primaryAddress()) { <em>— switch to another account first</em> }
            </div>
            <div class="wl-linkflow-actions">
              <button class="wl-btn-ghost" (click)="cancelLink()" [disabled]="busy()">Cancel</button>
              <button class="wl-cta wl-inline" (click)="signLink()"
                      [disabled]="busy() || !wallet.connected() || (wallet.publicKey() || '') === primaryAddress()">
                <lucide-icon name="pen-tool" [size]="15" />
                {{ busy() ? 'Linking…' : 'Sign to link' }}
              </button>
            </div>
          </section>
        }

        <!-- ── Add to your account ────────────────────────────── -->
        <section class="wl-card">
          <div class="wl-card-top"><span class="wl-lbl">Add to your account</span></div>

          <div class="wl-connect-grid">
            <!-- Solana — only when the account has no Solana wallet yet -->
            @if (!hasSolana()) {
              <!-- A Solana wallet already connected (Solana-primary account) uses
                   the switch-account link flow; otherwise (EVM-primary, nothing
                   connected) open a picker to connect + link one. -->
              <button class="wl-connect" (click)="wallet.connected() ? startLink() : openSolanaModal()" [disabled]="busy() || linkActive()">
                <div class="wl-tile" [style.background]="TYPE_META['solana_wallet'].tint"><app-brand-icon type="solana_wallet" [size]="22" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">Solana wallet</span>
                  <span class="wl-connect-sub">Connect</span>
                </div>
                <lucide-icon class="wl-connect-plus" name="plus" [size]="16" />
              </button>
            }

            <!-- Ethereum — one per account -->
            @if (!hasEvm()) {
              <button class="wl-connect" (click)="openEvmModal()" [disabled]="busy()">
                <div class="wl-tile" [style.background]="TYPE_META['evm_wallet'].tint"><app-brand-icon type="evm_wallet" [size]="22" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">Ethereum wallet</span>
                  <span class="wl-connect-sub">MetaMask &amp; more</span>
                </div>
                <lucide-icon class="wl-connect-plus" name="plus" [size]="16" />
              </button>
            } @else {
              <div class="wl-connect wl-connect-done">
                <div class="wl-tile" [style.background]="TYPE_META['evm_wallet'].tint"><app-brand-icon type="evm_wallet" [size]="22" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">Ethereum wallet</span>
                  <span class="wl-connect-sub">Connected</span>
                </div>
                <lucide-icon class="wl-connect-check" name="check" [size]="16" />
              </div>
            }

            <!-- Telegram -->
            @if (hasTelegram()) {
              <div class="wl-connect wl-connect-done">
                <div class="wl-tile" [style.background]="TYPE_META['telegram'].tint"><app-brand-icon type="telegram" [size]="22" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">Telegram</span>
                  <span class="wl-connect-sub">Connected</span>
                </div>
                <lucide-icon class="wl-connect-check" name="check" [size]="16" />
              </div>
            } @else {
              <button class="wl-connect" (click)="connectTelegram()" [disabled]="busy()">
                <div class="wl-tile" [style.background]="TYPE_META['telegram'].tint"><app-brand-icon type="telegram" [size]="22" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">Telegram</span>
                  <span class="wl-connect-sub">Connect</span>
                </div>
                <lucide-icon class="wl-connect-plus" name="plus" [size]="16" />
              </button>
            }

            <!-- X / Twitter (self-declared handle — auto-fills token launches) -->
            @if (xEditing()) {
              <div class="wl-connect wl-connect-edit">
                <div class="wl-tile" [style.background]="TYPE_META['twitter'].tint"><app-brand-icon type="twitter" [size]="20" /></div>
                <input class="wl-xinput" type="text" [value]="xInput()" placeholder="@handle or x.com/…"
                       [disabled]="busy()" (input)="xInput.set($any($event.target).value)"
                       (keydown.enter)="saveTwitter()" />
                <button class="wl-xbtn" (click)="saveTwitter()" [disabled]="busy()">{{ busy() ? '…' : 'Save' }}</button>
                <button class="wl-xbtn wl-xbtn-ghost" (click)="cancelEditX()" [disabled]="busy()">Cancel</button>
              </div>
            } @else if (hasTwitter()) {
              <div class="wl-connect wl-connect-done">
                <div class="wl-tile" [style.background]="TYPE_META['twitter'].tint"><app-brand-icon type="twitter" [size]="20" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">X (Twitter)</span>
                  <span class="wl-connect-sub">{{ twitterHandle() }}</span>
                </div>
                <button class="wl-iconbtn" (click)="startEditX()" [disabled]="busy()" title="Change">
                  <lucide-icon name="pencil" [size]="14" />
                </button>
                <button class="wl-iconbtn wl-unlink" (click)="removeTwitter()" [disabled]="busy()" title="Remove">
                  <lucide-icon name="x" [size]="14" />
                </button>
              </div>
            } @else {
              <button class="wl-connect" (click)="startEditX()" [disabled]="busy()">
                <div class="wl-tile" [style.background]="TYPE_META['twitter'].tint"><app-brand-icon type="twitter" [size]="20" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">X (Twitter)</span>
                  <span class="wl-connect-sub">Connect</span>
                </div>
                <lucide-icon class="wl-connect-plus" name="plus" [size]="16" />
              </button>
            }
          </div>

          @if (msg()) { <div class="wl-msg" [class.ok]="msgOk()">{{ msg() }}</div> }
        </section>
      }

      <!-- EVM wallet picker — same design as the Solana Connect modal -->
      @if (evmModalOpen()) {
        <div class="wallet-modal-overlay" (click)="onEvmBackdrop($event)">
          <div class="wallet-modal">
            <div class="wallet-modal-header">
              <div class="wallet-modal-header-text">
                <span class="wallet-modal-title">Link an Ethereum wallet</span>
                <span class="wallet-modal-subtitle">Choose a wallet to add to your OPRAI account.</span>
              </div>
              <button class="wallet-modal-close" (click)="closeEvmModal()" aria-label="Close">
                <lucide-icon name="x" [size]="16" />
              </button>
            </div>

            <div class="wallet-modal-body">
              @if (linkConsent(); as cw) {
                <!-- Consent: linking permanently binds the two wallets. -->
                <div class="link-consent">
                  <div class="link-consent-ico"><lucide-icon name="lock" [size]="22" /></div>
                  <div class="link-consent-title">Link {{ cw.name }} to your account</div>
                  <div class="link-consent-pair">
                    <span class="lc-chip"><app-brand-icon type="solana_wallet" [size]="16" /> Solana</span>
                    <lucide-icon name="arrow-right-left" [size]="14" />
                    <span class="lc-chip"><app-brand-icon type="evm_wallet" [size]="16" /> {{ cw.name }}</span>
                  </div>
                  <p class="link-consent-text">
                    You'll sign a message to prove you own this wallet. Once linked, these two
                    wallets are <b>permanently bound to one OPRAI account</b> — this is a one-time
                    action and <b>can't be undone</b>. Everything you hold, earn and trade rolls up
                    across both.
                  </p>
                  <div class="link-consent-actions">
                    <button class="wl-btn-ghost" (click)="cancelConsent()" [disabled]="busy()">Cancel</button>
                    <button class="wl-cta wl-inline" (click)="connectEvm(cw)" [disabled]="busy()">
                      <lucide-icon name="pen-tool" [size]="15" /> {{ busy() ? 'Linking…' : 'Sign to link' }}
                    </button>
                  </div>
                </div>
              } @else {
              @if (detectedEvm().length > 0) {
                <div class="wallet-section-label">Installed</div>
                <div class="wallet-list">
                  @for (w of detectedEvm(); track w.uuid) {
                    <button class="wallet-row" (click)="askConsent(w)" [disabled]="busy()">
                      <span class="wallet-row-icon" style="display:grid;place-items:center">
                        @if (w.icon && !iconFailed().has(w.uuid)) {
                          <img [src]="w.icon" [alt]="w.name" width="32" height="32" style="border-radius:8px" (error)="onIconError(w.uuid)" />
                        } @else {
                          <app-brand-icon type="evm_wallet" [size]="22" />
                        }
                      </span>
                      <span class="wallet-row-name">{{ w.name }}</span>
                      <span class="wallet-row-badge"><span class="wallet-row-dot"></span>Installed</span>
                    </button>
                  }
                </div>
              }
              @if (installableEvm().length > 0) {
                <div class="wallet-section-label">{{ detectedEvm().length > 0 ? 'More wallets' : 'Popular wallets' }}</div>
                <div class="wallet-list">
                  @for (w of installableEvm(); track w.name) {
                    <button class="wallet-row wallet-row--install" (click)="openInstallUrl(w.url)" [attr.title]="'Install ' + w.name">
                      <span class="wallet-row-icon" style="display:grid;place-items:center">
                        @if (!iconFailed().has(w.name)) {
                          <img [src]="w.icon" [alt]="w.name" width="30" height="30" style="border-radius:8px" (error)="onIconError(w.name)" />
                        } @else {
                          <app-brand-icon type="evm_wallet" [size]="22" />
                        }
                      </span>
                      <span class="wallet-row-name">{{ w.name }}</span>
                      <span class="wallet-row-install">Install</span>
                    </button>
                  }
                </div>
              }
              }
            </div>

            <div class="wallet-modal-footer">
              <span class="wallet-modal-footer-text">New to Ethereum wallets?</span>
              <a href="https://metamask.io/download/" target="_blank" rel="noopener noreferrer" class="wallet-modal-footer-link">Get MetaMask</a>
            </div>
          </div>
        </div>
      }

      <!-- Solana wallet picker — connect + link a Solana wallet. Used for
           EVM-primary accounts (no Solana wallet connected, so the switch-
           account flow can't start). -->
      @if (solanaModalOpen()) {
        <div class="wallet-modal-overlay" (click)="onSolanaBackdrop($event)">
          <div class="wallet-modal">
            <div class="wallet-modal-header">
              <div class="wallet-modal-header-text">
                <span class="wallet-modal-title">Link a Solana wallet</span>
                <span class="wallet-modal-subtitle">Choose a wallet to add. You'll sign a message to prove you own it — it's then permanently linked to your OPRAI account.</span>
              </div>
              <button class="wallet-modal-close" (click)="closeSolanaModal()" aria-label="Close">
                <lucide-icon name="x" [size]="16" />
              </button>
            </div>

            <div class="wallet-modal-body">
              @if (solanaDetected().length > 0) {
                <div class="wallet-section-label">Installed</div>
                <div class="wallet-list">
                  @for (w of solanaDetected(); track w.name) {
                    <button class="wallet-row" (click)="linkSolanaWallet(w.name)" [disabled]="busy()">
                      <span class="wallet-row-icon" style="display:grid;place-items:center">
                        @if (w.icon && !iconFailed().has(w.name)) {
                          <img [src]="w.icon" [alt]="w.name" width="32" height="32" style="border-radius:8px" (error)="onIconError(w.name)" />
                        } @else {
                          <app-brand-icon type="solana_wallet" [size]="22" />
                        }
                      </span>
                      <span class="wallet-row-name">{{ w.name }}</span>
                      <span class="wallet-row-badge"><span class="wallet-row-dot"></span>Installed</span>
                    </button>
                  }
                </div>
              }
              @if (solanaInstallable().length > 0) {
                <div class="wallet-section-label">{{ solanaDetected().length > 0 ? 'More wallets' : 'Popular wallets' }}</div>
                <div class="wallet-list">
                  @for (w of solanaInstallable(); track w.name) {
                    <button class="wallet-row wallet-row--install" (click)="openInstallUrl(w.url)" [attr.title]="'Install ' + w.name">
                      <span class="wallet-row-icon" style="display:grid;place-items:center">
                        @if (w.icon && !iconFailed().has(w.name)) {
                          <img [src]="w.icon" [alt]="w.name" width="30" height="30" style="border-radius:8px" (error)="onIconError(w.name)" />
                        } @else {
                          <app-brand-icon type="solana_wallet" [size]="22" />
                        }
                      </span>
                      <span class="wallet-row-name">{{ w.name }}</span>
                      <span class="wallet-row-install">Install</span>
                    </button>
                  }
                </div>
              }
              @if (busy()) {
                <div class="wallet-modal-footer-text" style="padding:10px 2px 2px">Approve the connection and signature in your wallet…</div>
              }
            </div>
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    :host { display:block; flex:1 1 auto; min-height:0; overflow-y:auto; }
    .wl { max-width:640px; margin:0 auto; padding:28px 20px 64px; display:flex; flex-direction:column; gap:16px; }
    .wl-head h1 { display:flex; align-items:center; gap:9px; font-size:1.4rem; font-weight:700; color:var(--op-text-primary); margin:0 0 5px; }
    .wl-head p { color:var(--op-text-secondary); margin:0; font-size:.88rem; max-width:56ch; line-height:1.5; }

    .wl-card { background:var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:16px; padding:16px; }
    .wl-card-top { display:flex; align-items:center; gap:8px; margin-bottom:12px; }
    .wl-lbl { font-size:.7rem; text-transform:uppercase; letter-spacing:.06em; font-weight:600; color:var(--op-text-secondary); }
    .wl-count { font-size:.72rem; font-weight:600; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.1)); min-width:20px; text-align:center; padding:1px 8px; border-radius:999px; }

    .wl-list { display:flex; flex-direction:column; gap:6px; }
    .wl-row { display:flex; align-items:center; gap:13px; padding:11px 12px; border-radius:12px; transition:background .12s; }
    .wl-row:hover { background:var(--op-bg-surface-2, rgba(125,125,150,.05)); }
    .wl-tile { width:40px; height:40px; border-radius:11px; flex:none; display:grid; place-items:center; }
    .wl-main { flex:1; min-width:0; }
    .wl-name { font-size:.92rem; font-weight:600; color:var(--op-text-primary); display:flex; align-items:center; gap:8px; }
    .wl-primary { font-size:.6rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:#22c55e; background:color-mix(in srgb, #22c55e 16%, transparent); padding:1px 7px; border-radius:6px; }
    .wl-addr { display:inline-flex; align-items:center; gap:6px; background:none; border:0; padding:3px 0 0; cursor:pointer; font-family:ui-monospace,monospace; font-size:.8rem; color:var(--op-text-secondary); }
    .wl-addr:hover { color:var(--op-brand,#5b5fc7); }
    .wl-actions { display:flex; align-items:center; gap:6px; flex:none; }
    .wl-iconbtn { width:32px; height:32px; border-radius:9px; display:grid; place-items:center; background:transparent; border:1px solid var(--op-border, rgba(255,255,255,.1)); color:var(--op-text-secondary); cursor:pointer; transition:.12s; }
    .wl-iconbtn:disabled { opacity:.4; cursor:not-allowed; }
    .wl-star:hover:not(:disabled) { color:#22c55e; border-color:#22c55e; }
    .wl-unlink:hover:not(:disabled) { color:#ef4444; border-color:#ef4444; }
    .wl-primary-lock { width:32px; height:32px; display:grid; place-items:center; color:#22c55e; }

    /* Connect list — full-width rows so socials (Telegram widget) have room */
    .wl-connect-grid { display:flex; flex-direction:column; gap:8px; }
    .wl-connect { display:flex; align-items:center; gap:12px; padding:12px; border:1px solid var(--op-border, rgba(255,255,255,.09)); border-radius:13px; background:transparent; color:var(--op-text-primary); cursor:pointer; text-align:left; transition:.14s; }
    button.wl-connect:hover:not(:disabled) { border-color:var(--op-brand,#5b5fc7); transform:translateY(-1px); }
    .wl-connect:disabled { opacity:.55; cursor:not-allowed; }
    .wl-connect-text { flex:1; min-width:0; display:flex; flex-direction:column; gap:1px; }
    .wl-connect-title { font-size:.9rem; font-weight:600; color:var(--op-text-primary); }
    .wl-connect-sub { font-size:.72rem; color:var(--op-text-secondary); }
    .wl-connect-plus { color:var(--op-text-secondary); flex:none; }
    .wl-connect-check { color:#22c55e; flex:none; }
    .wl-connect-done { cursor:default; }
    .wl-connect-widget { cursor:default; }
    .wl-connect-soon { cursor:not-allowed; opacity:.7; }
    .wl-soon { font-size:.6rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.14)); padding:2px 7px; border-radius:6px; flex:none; }
    .wl-connect-edit { cursor:default; }
    .wl-xinput { flex:1; min-width:0; background:var(--op-bg-surface-2, rgba(125,125,150,.08)); border:1px solid var(--op-border, rgba(255,255,255,.12)); border-radius:9px; padding:8px 10px; color:var(--op-text-primary); font-size:.85rem; outline:none; }
    .wl-xinput:focus { border-color:var(--op-brand, #5b5fc7); }
    .wl-xbtn { flex:none; border:0; border-radius:9px; padding:8px 12px; font-size:.8rem; font-weight:600; cursor:pointer; background:var(--op-brand, #5b5fc7); color:#fff; }
    .wl-xbtn-ghost { background:var(--op-bg-surface-2, rgba(125,125,150,.14)); color:var(--op-text-secondary); }
    .wl-xbtn:disabled { opacity:.5; cursor:default; }
    .wl-tg-widget { flex:none; display:flex; align-items:center; min-height:28px; }
    .wl-tg-widget iframe { color-scheme:normal; }
    /* Link consent */
    .link-consent { padding:6px 4px 4px; text-align:center; }
    .link-consent-ico { width:44px; height:44px; border-radius:12px; margin:0 auto 12px; display:grid; place-items:center; color:var(--op-brand,#5b5fc7); background:color-mix(in srgb, var(--op-brand,#5b5fc7) 14%, transparent); }
    .link-consent-title { font-size:1rem; font-weight:700; color:var(--op-text-primary); margin-bottom:12px; }
    .link-consent-pair { display:flex; align-items:center; justify-content:center; gap:10px; color:var(--op-text-secondary); margin-bottom:14px; }
    .lc-chip { display:inline-flex; align-items:center; gap:6px; font-size:.82rem; font-weight:600; color:var(--op-text-primary); background:var(--op-bg-surface-2, rgba(125,125,150,.1)); padding:5px 10px; border-radius:9px; }
    .link-consent-text { font-size:.85rem; line-height:1.55; color:var(--op-text-secondary); max-width:44ch; margin:0 auto 18px; }
    .link-consent-text b { color:var(--op-text-primary); }
    .link-consent-actions { display:flex; gap:10px; justify-content:center; }

    /* Link flow */
    .wl-flowcard { background:var(--op-bg-surface-2, rgba(125,125,150,.05)); }
    .wl-linkflow-head { display:flex; align-items:center; gap:8px; font-weight:600; font-size:.92rem; color:var(--op-text-primary); margin-bottom:10px; }
    .wl-steps { margin:0 0 12px; padding-left:20px; color:var(--op-text-secondary); font-size:.83rem; display:flex; flex-direction:column; gap:5px; }
    .wl-steps b { color:var(--op-text-primary); }
    .wl-connected-now { font-size:.8rem; color:var(--op-text-secondary); margin-bottom:12px; }
    .wl-connected-now span { font-family:ui-monospace,monospace; color:var(--op-text-primary); }
    .wl-connected-now em { color:#f59e0b; font-style:normal; }
    .wl-linkflow-actions { display:flex; gap:10px; justify-content:flex-end; align-items:center; }
    .wl-cta { display:inline-flex; align-items:center; justify-content:center; gap:8px; background:linear-gradient(90deg,#5b5fc7,#06b6d4); color:#fff; border:0; border-radius:10px; font-weight:600; font-size:.88rem; cursor:pointer; }
    .wl-cta:disabled { opacity:.5; cursor:not-allowed; }
    .wl-inline { padding:9px 16px; }
    .wl-btn-ghost { background:transparent; border:1px solid var(--op-border, rgba(255,255,255,.12)); color:var(--op-text-secondary); border-radius:10px; padding:9px 14px; font-size:.85rem; cursor:pointer; }
    .wl-btn-ghost:hover { color:var(--op-text-primary); }
    .wl-btn-ghost:disabled { opacity:.5; cursor:not-allowed; }

    .wl-msg { font-size:.83rem; margin-top:12px; color:#ef4444; }
    .wl-msg.ok { color:#22c55e; }
    .wl-error { color:#ef4444; display:flex; gap:10px; align-items:center; font-size:.9rem; }
    .wl-skel .sk { height:76px; border-radius:16px; margin-bottom:12px; background:linear-gradient(90deg, rgba(125,125,150,.08), rgba(125,125,150,.16), rgba(125,125,150,.08)); background-size:200% 100%; animation:sh 1.3s infinite; }
    @keyframes sh { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
  `],
})
export class WalletsComponent implements OnInit, OnDestroy {
  private account = inject(AccountService);
  readonly wallet = inject(WalletService);
  private zone = inject(NgZone);

  readonly TYPE_META = TYPE_META;

  identities = signal<LinkedIdentity[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  busy = signal(false);
  msg = signal<string | null>(null);
  msgOk = signal(false);
  copied = signal<string | null>(null);
  linkActive = signal(false);
  primaryAddress = signal<string>('');
  hasTelegram = computed(() => this.identities().some((i) => i.type === 'telegram'));
  hasSolana = computed(() => this.identities().some((i) => i.type === 'solana_wallet'));
  hasEvm = computed(() => this.identities().some((i) => i.type === 'evm_wallet'));
  // X / Twitter (self-declared handle, no OAuth). twitterId is the identity row;
  // twitterHandle is the "@name" shown in the UI.
  twitterId = computed(() => this.identities().find((i) => i.type === 'twitter'));
  hasTwitter = computed(() => !!this.twitterId());
  twitterHandle = computed(() => {
    const url = this.twitterId()?.identifier ?? '';
    const m = url.match(/(?:x\.com|twitter\.com)\/@?([A-Za-z0-9_]+)/i);
    return m ? '@' + m[1] : (url ? '@' + url.replace(/^@/, '') : '');
  });
  xInput = signal('');
  xEditing = signal(false);
  evmModalOpen = signal(false);
  detectedEvm = signal<EvmWallet[]>([]);
  linkConsent = signal<EvmWallet | null>(null);
  iconFailed = signal<Set<string>>(new Set());
  // Solana wallet picker (for linking Solana to an EVM-primary account, where
  // no Solana wallet is connected so the switch-account flow can't start).
  solanaModalOpen = signal(false);
  solanaWallets = signal<{ name: string; icon: string; detected: boolean; url: string }[]>([]);
  readonly solanaDetected = computed(() => this.solanaWallets().filter((w) => w.detected));
  readonly solanaInstallable = computed(() => this.solanaWallets().filter((w) => !w.detected));

  askConsent(w: EvmWallet): void { this.linkConsent.set(w); }
  cancelConsent(): void { this.linkConsent.set(null); }
  // The full popular list minus anything already detected (case-insensitive).
  installableEvm = computed(() => {
    const have = new Set(this.detectedEvm().map((w) => w.name.toLowerCase().replace(/\s+wallet$/, '')));
    return INSTALLABLE_EVM.filter((w) => !have.has(w.name.toLowerCase().replace(/\s+wallet$/, '')));
  });

  onIconError(uuid: string): void {
    const s = new Set(this.iconFailed());
    s.add(uuid);
    this.iconFailed.set(s);
  }

  private tgMessageHandler?: (e: MessageEvent) => void;

  ngOnInit(): void { this.load(); }

  ngOnDestroy(): void {
    // Never leave linking mode armed if the user navigates away mid-flow —
    // otherwise a later wallet switch would silently keep the old session.
    this.wallet.setLinkingMode(false);
    if (this.tgMessageHandler) window.removeEventListener('message', this.tgMessageHandler);
  }

  /** Open Telegram's OAuth popup (bot must have its domain set to app.oprai.xyz
   *  in @BotFather). On success oauth.telegram.org postMessages the signed user
   *  back to us; we forward it to the backend, which HMAC-verifies it. A popup
   *  (vs the embedded widget) gives a real, clickable button with feedback. */
  connectTelegram(): void {
    if (this.busy()) return;
    const origin = window.location.origin;
    const url = `https://oauth.telegram.org/auth?bot_id=${TELEGRAM_BOT_ID}` +
      `&origin=${encodeURIComponent(origin)}&request_access=write&return_to=${encodeURIComponent(origin)}`;
    const wdt = 550, hgt = 500;
    const left = Math.max(0, (window.screen.width - wdt) / 2);
    const top = Math.max(0, (window.screen.height - hgt) / 2);
    const popup = window.open(url, 'oprai_telegram_login', `width=${wdt},height=${hgt},left=${left},top=${top}`);
    if (!popup) { this.flash('Please allow the popup to connect Telegram.', false); return; }

    if (this.tgMessageHandler) window.removeEventListener('message', this.tgMessageHandler);
    this.tgMessageHandler = (e: MessageEvent) => {
      if (e.origin !== 'https://oauth.telegram.org') return;
      let data: { event?: string; result?: Record<string, unknown> } | null = null;
      try { data = typeof e.data === 'string' ? JSON.parse(e.data) : e.data; } catch { return; }
      if (data?.event === 'auth_result' && data.result) {
        window.removeEventListener('message', this.tgMessageHandler!);
        this.zone.run(() => this.onTelegramAuth(data!.result!));
      }
    };
    window.addEventListener('message', this.tgMessageHandler);
  }

  private onTelegramAuth(user: Record<string, unknown>): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.msg.set(null);
    this.account.linkTelegram(user).subscribe({
      next: (a) => {
        this.identities.set(a.identities || []);
        this.busy.set(false);
        this.flash(a.alreadyLinked ? 'That Telegram account is already linked.' : 'Telegram connected!', true);
      },
      error: () => { this.busy.set(false); this.flash('Could not connect Telegram.', false); },
    });
  }

  startEditX(): void { this.xInput.set(this.twitterHandle()); this.xEditing.set(true); }
  cancelEditX(): void { this.xEditing.set(false); this.xInput.set(''); }

  saveTwitter(): void {
    if (this.busy()) return;
    const handle = this.xInput().trim();
    if (!handle) { this.flash('Enter your X handle or profile URL.', false); return; }
    this.busy.set(true); this.msg.set(null);
    this.account.setTwitter(handle).subscribe({
      next: (a) => {
        this.identities.set(a.identities || []);
        this.busy.set(false); this.xEditing.set(false); this.xInput.set('');
        this.flash('X profile saved!', true);
      },
      error: (e) => { this.busy.set(false); this.flash(e?.error?.error || 'That doesn\'t look like a valid X handle.', false); },
    });
  }

  removeTwitter(): void {
    if (this.busy()) return;
    this.busy.set(true); this.msg.set(null);
    this.account.setTwitter('').subscribe({
      next: (a) => { this.identities.set(a.identities || []); this.busy.set(false); this.flash('X profile removed.', true); },
      error: () => { this.busy.set(false); this.flash('Could not remove X profile.', false); },
    });
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.account.getMe().subscribe({
      next: (a) => { this.identities.set(a.identities || []); this.loading.set(false); },
      error: () => { this.error.set('Could not load your wallets.'); this.loading.set(false); },
    });
  }

  meta(id: LinkedIdentity) { return TYPE_META[id.type] ?? { label: id.type, color: '#7e8298', tint: 'rgba(125,125,150,.12)' }; }

  shortId(s: string): string {
    return s.length > 16 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
  }

  /** What to show under the identity name: a truncated address for wallets, a
   *  @handle for socials. */
  display(id: LinkedIdentity): string {
    if (id.type === 'telegram') return id.label ? `@${id.label}` : `Telegram #${id.identifier}`;
    if (id.type === 'twitter') return id.label ? `@${id.label}` : id.identifier;
    return this.shortId(id.identifier);
  }

  copy(s: string): void {
    navigator.clipboard?.writeText(s);
    this.copied.set(s);
    setTimeout(() => this.copied.set(null), 1400);
  }

  unlink(id: LinkedIdentity): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.msg.set(null);
    this.account.unlink(id.id).subscribe({
      next: (a) => { this.identities.set(a.identities || []); this.busy.set(false); this.flash('Wallet removed.', true); },
      error: () => { this.busy.set(false); this.flash('Could not remove that wallet.', false); },
    });
  }

  /** Begin the guided flow: remember the primary wallet, arm linking mode so the
   *  upcoming account switch doesn't log the session out. */
  startLink(): void {
    if (!this.wallet.connected()) return;
    this.msg.set(null);
    this.primaryAddress.set(this.wallet.publicKey() || '');
    this.wallet.setLinkingMode(true);
    this.linkActive.set(true);
  }

  cancelLink(): void {
    this.wallet.setLinkingMode(false);
    this.linkActive.set(false);
    this.msg.set(null);
  }

  /** Sign the link challenge with the CURRENTLY selected (new) wallet and attach
   *  it to the account. The primary session is untouched. */
  async signLink(): Promise<void> {
    const address = this.wallet.publicKey();
    if (this.busy() || !address || address === this.primaryAddress()) return;
    this.busy.set(true);
    this.msg.set(null);
    try {
      const nz = await firstValueFrom(this.account.linkNonce());
      const sigBytes = await this.wallet.signMessage(new TextEncoder().encode(`OPRAI link wallet: ${nz.nonce}`));
      const signature = bs58.encode(sigBytes);
      const res = await firstValueFrom(this.account.linkVerify(address, signature, nz.nonceId));
      this.identities.set(res.identities || []);
      this.linkActive.set(false);
      this.wallet.setLinkingMode(false);
      this.flash(
        res.alreadyLinked
          ? 'That wallet is already on your account. Switch back to your primary wallet to continue.'
          : 'Wallet linked! Switch back to your primary wallet in your extension to keep trading.',
        true,
      );
    } catch {
      this.flash('Could not link that wallet. Make sure you switched accounts, then try again.', false);
    } finally {
      this.busy.set(false);
    }
  }

  makePrimary(id: LinkedIdentity): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.msg.set(null);
    this.account.setPrimary(id.id).subscribe({
      next: (a) => {
        this.identities.set(a.identities || []);
        this.busy.set(false);
        this.flash('Primary wallet updated. Log in with it next time to land on this account.', true);
      },
      error: () => { this.busy.set(false); this.flash('Could not change your primary wallet.', false); },
    });
  }

  /** Open the EVM wallet picker — discover installed wallets via EIP-6963 (the
   *  multi-wallet standard) so the user chooses, exactly like the Solana modal,
   *  instead of an injected wallet auto-popping. */
  openEvmModal(): void {
    if (this.busy()) return;
    this.msg.set(null);
    this.detectedEvm.set([]);
    this.linkConsent.set(null);
    this.evmModalOpen.set(true);

    // EIP-6963: wallets announce (name + icon + provider) in response to our
    // request. Some announce asynchronously, so we listen for a short window and
    // update the list live as each wallet reports in.
    const found = new Map<string, EvmWallet>();
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { info?: { uuid: string; name: string; icon: string; rdns?: string }; provider?: EthProvider }
        | undefined;
      if (detail?.info && detail.provider) {
        found.set(detail.info.uuid, {
          uuid: detail.info.uuid, name: detail.info.name, icon: detail.info.icon,
          rdns: detail.info.rdns, provider: detail.provider, detected: true,
        });
        this.zone.run(() => this.detectedEvm.set([...found.values()]));
      }
    };
    window.addEventListener('eip6963:announceProvider', handler);
    window.dispatchEvent(new Event('eip6963:requestProvider'));
    setTimeout(() => {
      window.removeEventListener('eip6963:announceProvider', handler);
      // Legacy fallback: a pre-6963 wallet only exposes window.ethereum.
      if (found.size === 0) {
        const legacy = (window as unknown as { ethereum?: EthProvider }).ethereum;
        if (legacy?.request) {
          this.zone.run(() => this.detectedEvm.set([
            { uuid: 'legacy', name: 'Browser wallet', icon: '', provider: legacy, detected: true },
          ]));
        }
      }
    }, 350);
  }

  readonly INSTALLABLE_EVM = INSTALLABLE_EVM;

  closeEvmModal(): void {
    this.evmModalOpen.set(false);
    this.linkConsent.set(null);
  }

  onEvmBackdrop(ev: MouseEvent): void {
    if ((ev.target as HTMLElement).classList.contains('wallet-modal-overlay')) this.closeEvmModal();
  }

  openInstallUrl(url: string): void {
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  /** Connect the chosen EVM wallet and link it via EIP-191 personal_sign — no
   *  adapter, so the Solana session is untouched. */
  async connectEvm(entry: EvmWallet): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.msg.set(null);
    try {
      const provider = entry.provider;
      const accounts = (await provider.request({ method: 'eth_requestAccounts' })) as string[];
      const address = accounts?.[0];
      if (!address) throw new Error('no evm account');
      const nz = await firstValueFrom(this.account.linkNonce());
      const message = `OPRAI link wallet: ${nz.nonce}`;
      const signature = (await provider.request({ method: 'personal_sign', params: [message, address] })) as string;
      const res = await firstValueFrom(this.account.linkEVMVerify(address, signature, nz.nonceId));
      this.identities.set(res.identities || []);
      // Remember this wallet's identity so its bridges/swaps sign with IT, not
      // whatever owns window.ethereum (MetaMask usually wins that).
      this.wallet.rememberEvmWallet(entry.rdns, address);
      this.evmModalOpen.set(false);
      this.linkConsent.set(null);
      this.flash(res.alreadyLinked ? `That ${entry.name} wallet is already on your account.` : `${entry.name} permanently linked to your account.`, true);
    } catch (e) {
      const rejected = (e as { code?: number })?.code === 4001;
      this.flash(rejected ? 'Signature request was rejected.' : 'Could not link that Ethereum wallet.', false);
    } finally {
      this.busy.set(false);
    }
  }

  // ── Solana wallet picker (link to an EVM-primary account) ────────────────
  /** Open the Solana wallet picker. Used when NO Solana wallet is connected
   *  (e.g. the account signed in with EVM), so the switch-account link flow
   *  can't run — here the user picks a Solana wallet to connect and link. */
  openSolanaModal(): void {
    if (this.busy()) return;
    this.msg.set(null);
    this.solanaWallets.set(this.wallet.getWallets(true).map((w) => ({
      name: w.name, icon: w.icon, detected: w.detected, url: w.url,
    })));
    this.solanaModalOpen.set(true);
  }
  closeSolanaModal(): void { this.solanaModalOpen.set(false); }
  onSolanaBackdrop(ev: MouseEvent): void {
    if ((ev.target as HTMLElement).classList.contains('wallet-modal-overlay')) this.closeSolanaModal();
  }

  /** Connect the chosen Solana wallet and link it to the account. Safe for an
   *  EVM-primary session: the account is already authenticated, so the auto
   *  sign-in effect stays dormant (it returns early while authed), and linking
   *  mode suppresses the account-switch teardown — the EVM session is untouched. */
  async linkSolanaWallet(name: string): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.msg.set(null);
    this.wallet.setLinkingMode(true);
    try {
      await this.wallet.connect(name);
      const address = this.wallet.publicKey();
      if (!address) throw new Error('no solana account');
      if (this.identities().some((i) => i.identifier === address)) {
        this.solanaModalOpen.set(false);
        this.flash('That wallet is already on your account.', true);
        return;
      }
      const nz = await firstValueFrom(this.account.linkNonce());
      const sigBytes = await this.wallet.signMessage(new TextEncoder().encode(`OPRAI link wallet: ${nz.nonce}`));
      const signature = bs58.encode(sigBytes);
      const res = await firstValueFrom(this.account.linkVerify(address, signature, nz.nonceId));
      this.identities.set(res.identities || []);
      this.solanaModalOpen.set(false);
      this.flash(res.alreadyLinked ? 'That wallet is already on your account.' : 'Solana wallet linked to your account.', true);
    } catch {
      this.flash('Could not link that Solana wallet. Approve the connection and the signature, then try again.', false);
    } finally {
      this.wallet.setLinkingMode(false);
      this.busy.set(false);
    }
  }

  private flash(m: string, ok: boolean): void {
    this.msgOk.set(ok);
    this.msg.set(m);
  }
}

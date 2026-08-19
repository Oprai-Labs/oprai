import {
  Component, ElementRef, NgZone, OnDestroy, OnInit,
  ViewChild, computed, inject, signal,
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

// The Telegram Login Widget calls a GLOBAL callback with the authorised user.
const TELEGRAM_BOT = 'Oprai_Labs_Bot';
const TELEGRAM_CB = 'onOpraiTelegramAuth';

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
                    @if (id.type === 'solana_wallet') {
                      <button class="wl-iconbtn wl-star" (click)="makePrimary(id)" [disabled]="busy()" title="Make primary">
                        <lucide-icon name="shield-check" [size]="15" />
                      </button>
                    }
                    <button class="wl-iconbtn wl-unlink" (click)="unlink(id)" [disabled]="busy()" title="Unlink">
                      <lucide-icon name="trash-2" [size]="15" />
                    </button>
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
            <!-- Solana -->
            <button class="wl-connect" (click)="startLink()" [disabled]="busy() || linkActive() || !wallet.connected()">
              <div class="wl-tile" [style.background]="TYPE_META['solana_wallet'].tint"><app-brand-icon type="solana_wallet" [size]="22" /></div>
              <div class="wl-connect-text">
                <span class="wl-connect-title">Solana wallet</span>
                <span class="wl-connect-sub">Link another</span>
              </div>
              <lucide-icon class="wl-connect-plus" name="plus" [size]="16" />
            </button>

            <!-- Ethereum -->
            <button class="wl-connect" (click)="linkEVM()" [disabled]="busy()">
              <div class="wl-tile" [style.background]="TYPE_META['evm_wallet'].tint"><app-brand-icon type="evm_wallet" [size]="22" /></div>
              <div class="wl-connect-text">
                <span class="wl-connect-title">Ethereum wallet</span>
                <span class="wl-connect-sub">MetaMask &amp; more</span>
              </div>
              <lucide-icon class="wl-connect-plus" name="plus" [size]="16" />
            </button>

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
              <div class="wl-connect wl-connect-widget">
                <div class="wl-tile" [style.background]="TYPE_META['telegram'].tint"><app-brand-icon type="telegram" [size]="22" /></div>
                <div class="wl-connect-text">
                  <span class="wl-connect-title">Telegram</span>
                  <span class="wl-connect-sub">Connect</span>
                </div>
                <div #tgWidget class="wl-tg-widget"></div>
              </div>
            }

            <!-- X / Twitter -->
            <div class="wl-connect wl-connect-soon" title="Coming soon">
              <div class="wl-tile" [style.background]="TYPE_META['twitter'].tint"><app-brand-icon type="twitter" [size]="20" /></div>
              <div class="wl-connect-text">
                <span class="wl-connect-title">X (Twitter)</span>
                <span class="wl-connect-sub">Connect</span>
              </div>
              <span class="wl-soon">Soon</span>
            </div>
          </div>

          @if (msg()) { <div class="wl-msg" [class.ok]="msgOk()">{{ msg() }}</div> }
        </section>
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

    /* Connect grid */
    .wl-connect-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    @media (max-width:520px) { .wl-connect-grid { grid-template-columns:1fr; } }
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
    .wl-tg-widget { flex:none; display:flex; align-items:center; min-height:28px; }
    .wl-tg-widget iframe { color-scheme:normal; }

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

  // Fires when the widget container enters the view (after load resolves and the
  // @if gates open) — the reliable moment to inject the Telegram script.
  @ViewChild('tgWidget') set tgWidgetRef(ref: ElementRef<HTMLDivElement> | undefined) {
    if (ref) { this.tgWidget = ref; this.mountTelegram(); }
  }
  private tgWidget?: ElementRef<HTMLDivElement>;

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

  private tgMounted = false;

  ngOnInit(): void { this.load(); }

  ngOnDestroy(): void {
    // Never leave linking mode armed if the user navigates away mid-flow —
    // otherwise a later wallet switch would silently keep the old session.
    this.wallet.setLinkingMode(false);
    delete (window as unknown as Record<string, unknown>)[TELEGRAM_CB];
  }

  /** Inject the Telegram Login Widget script into its container once. Telegram
   *  renders its own button (an oauth.telegram.org iframe); on success it calls
   *  our global callback, which we bounce into the Angular zone. */
  private mountTelegram(): void {
    if (this.tgMounted || this.hasTelegram() || !this.tgWidget) return;
    this.tgMounted = true;
    (window as unknown as Record<string, unknown>)[TELEGRAM_CB] =
      (user: Record<string, unknown>) => this.zone.run(() => this.onTelegramAuth(user));
    const s = document.createElement('script');
    s.async = true;
    s.src = 'https://telegram.org/js/telegram-widget.js?22';
    s.setAttribute('data-telegram-login', TELEGRAM_BOT);
    s.setAttribute('data-size', 'medium');
    s.setAttribute('data-radius', '8');
    s.setAttribute('data-onauth', `${TELEGRAM_CB}(user)`);
    s.setAttribute('data-request-access', 'write');
    this.tgWidget.nativeElement.appendChild(s);
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

  /** Link an EVM wallet via the injected provider (MetaMask & friends). Uses
   *  EIP-191 personal_sign — no wallet adapter, so the Solana session is
   *  untouched and no linking-mode gymnastics are needed. */
  async linkEVM(): Promise<void> {
    if (this.busy()) return;
    const eth = (window as unknown as { ethereum?: EthProvider }).ethereum;
    if (!eth?.request) {
      this.flash('No Ethereum wallet detected. Install MetaMask to link one.', false);
      return;
    }
    this.busy.set(true);
    this.msg.set(null);
    try {
      const accounts = (await eth.request({ method: 'eth_requestAccounts' })) as string[];
      const address = accounts?.[0];
      if (!address) throw new Error('no evm account');
      const nz = await firstValueFrom(this.account.linkNonce());
      const message = `OPRAI link wallet: ${nz.nonce}`;
      const signature = (await eth.request({ method: 'personal_sign', params: [message, address] })) as string;
      const res = await firstValueFrom(this.account.linkEVMVerify(address, signature, nz.nonceId));
      this.identities.set(res.identities || []);
      this.flash(res.alreadyLinked ? 'That Ethereum wallet is already on your account.' : 'Ethereum wallet linked!', true);
    } catch (e) {
      const rejected = (e as { code?: number })?.code === 4001;
      this.flash(rejected ? 'Signature request was rejected.' : 'Could not link that Ethereum wallet.', false);
    } finally {
      this.busy.set(false);
    }
  }

  private flash(m: string, ok: boolean): void {
    this.msgOk.set(ok);
    this.msg.set(m);
  }
}

interface EthProvider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
}

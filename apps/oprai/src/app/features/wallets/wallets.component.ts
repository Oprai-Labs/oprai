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

const TYPE_META: Record<string, { label: string; color: string; short: string }> = {
  solana_wallet: { label: 'Solana wallet', color: '#8a5cf6', short: 'SOL' },
  evm_wallet: { label: 'EVM wallet', color: '#627eea', short: 'EVM' },
  telegram: { label: 'Telegram', color: '#1f96cf', short: 'TG' },
  email: { label: 'Email', color: '#5b5fc7', short: '@' },
};

// The Telegram Login Widget calls a GLOBAL callback with the authorised user.
const TELEGRAM_BOT = 'Oprai_Labs_Bot';
const TELEGRAM_CB = 'onOpraiTelegramAuth';

@Component({
  selector: 'app-wallets',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  template: `
    <div class="wl">
      <header class="wl-head">
        <h1><lucide-icon name="wallet" [size]="22" /> Wallets</h1>
        <p>One OPRAI account, all your wallets. Everything you trade, earn, and hold rolls up here — across every linked wallet.</p>
      </header>

      @if (loading()) {
        <div class="wl-skel"><div class="sk"></div><div class="sk"></div></div>
      } @else if (error()) {
        <div class="wl-error"><lucide-icon name="triangle-alert" [size]="16" /> {{ error() }} <button (click)="load()">Retry</button></div>
      } @else {
        <section class="wl-card">
          <div class="wl-card-top">
            <span class="wl-lbl">Linked identities</span>
            <span class="wl-count">{{ identities().length }}</span>
          </div>

          <div class="wl-list">
            @for (id of identities(); track id.id) {
              <div class="wl-row">
                <div class="wl-ico" [style.--tc]="meta(id).color">{{ meta(id).short }}</div>
                <div class="wl-main">
                  <div class="wl-line1">
                    {{ meta(id).label }}
                    @if (id.isPrimary) { <span class="wl-primary">Primary</span> }
                  </div>
                  <button class="wl-addr" (click)="copy(id.identifier)" title="Copy">
                    {{ shortId(id.identifier) }}
                    <lucide-icon [name]="copied() === id.identifier ? 'check' : 'copy'" [size]="13" />
                  </button>
                </div>
                @if (!id.isPrimary) {
                  @if (id.type === 'solana_wallet') {
                    <button class="wl-star" (click)="makePrimary(id)" [disabled]="busy()" title="Make primary">
                      <lucide-icon name="shield-check" [size]="16" />
                    </button>
                  }
                  <button class="wl-unlink" (click)="unlink(id)" [disabled]="busy()" title="Unlink">
                    <lucide-icon name="trash-2" [size]="16" />
                  </button>
                } @else {
                  <span class="wl-primary-lock" title="Your primary login wallet"><lucide-icon name="badge-check" [size]="16" /></span>
                }
              </div>
            }
          </div>

          @if (!linkActive()) {
            <button class="wl-add" (click)="startLink()" [disabled]="busy() || !wallet.connected()">
              <lucide-icon name="plus" [size]="16" />
              Link another wallet
            </button>
            <p class="wl-hint">Add a second Solana wallet to this same account. EVM &amp; Telegram linking are coming next.</p>
          } @else {
            <div class="wl-linkflow">
              <div class="wl-linkflow-head"><lucide-icon name="arrow-right-left" [size]="15" /> Link a wallet</div>
              <ol class="wl-steps">
                <li>Open your wallet extension and <b>switch to the account you want to add</b>.</li>
                <li>Come back and sign — this proves you own it. Your session stays on your primary wallet.</li>
              </ol>
              <div class="wl-connected-now">
                Currently selected: <span>{{ shortId(wallet.publicKey() || '—') }}</span>
                @if ((wallet.publicKey() || '') === primaryAddress()) { <em>(your primary — switch to another account first)</em> }
              </div>
              <div class="wl-linkflow-actions">
                <button class="wl-btn-ghost" (click)="cancelLink()" [disabled]="busy()">Cancel</button>
                <button class="wl-add wl-inline" (click)="signLink()"
                        [disabled]="busy() || !wallet.connected() || (wallet.publicKey() || '') === primaryAddress()">
                  <lucide-icon name="pen-tool" [size]="15" />
                  {{ busy() ? 'Linking…' : 'Sign to link' }}
                </button>
              </div>
            </div>
          }
          <div class="wl-other">
            <button class="wl-chain-btn" (click)="linkEVM()" [disabled]="busy()">
              <span class="wl-chain-ico" style="--tc:#627eea">EVM</span>
              <span>Link an Ethereum wallet</span>
              <lucide-icon name="plus" [size]="15" />
            </button>
            @if (!hasTelegram()) {
              <div class="wl-chain-btn wl-tg-row">
                <span class="wl-chain-ico" style="--tc:#1f96cf">TG</span>
                <span>Connect Telegram</span>
                <div #tgWidget class="wl-tg-widget"></div>
              </div>
            }
          </div>

          @if (msg()) { <div class="wl-msg" [class.ok]="msgOk()">{{ msg() }}</div> }
        </section>
      }
    </div>
  `,
  styles: [`
    :host { display:block; flex:1 1 auto; min-height:0; overflow-y:auto; }
    .wl { max-width:720px; margin:0 auto; padding:24px 20px 56px; }
    .wl-head h1 { display:flex; align-items:center; gap:10px; font-size:1.5rem; font-weight:700; color:var(--op-text-primary); margin:0 0 6px; }
    .wl-head p { color:var(--op-text-secondary); margin:0 0 24px; font-size:.9rem; max-width:60ch; }
    .wl-card { background:var(--op-bg-surface-1); border:1px solid var(--op-border, rgba(255,255,255,.08)); border-radius:18px; padding:20px; }
    .wl-card-top { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
    .wl-lbl { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--op-text-secondary); }
    .wl-count { font-family:monospace; font-size:.85rem; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.08)); padding:2px 9px; border-radius:999px; }
    .wl-list { display:flex; flex-direction:column; gap:8px; }
    .wl-row { display:flex; align-items:center; gap:12px; padding:12px; border:1px solid var(--op-border, rgba(255,255,255,.06)); border-radius:12px; }
    .wl-ico { width:36px; height:36px; border-radius:10px; flex:none; display:grid; place-items:center; font-size:.66rem; font-weight:700; color:#fff; background:var(--tc); letter-spacing:.02em; }
    .wl-main { flex:1; min-width:0; }
    .wl-line1 { font-size:.92rem; font-weight:600; color:var(--op-text-primary); display:flex; align-items:center; gap:8px; }
    .wl-primary { font-size:.62rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:#22c55e; background:color-mix(in srgb, #22c55e 16%, transparent); padding:1px 7px; border-radius:6px; }
    .wl-addr { display:inline-flex; align-items:center; gap:6px; background:none; border:0; padding:2px 0 0; cursor:pointer; font-family:monospace; font-size:.82rem; color:var(--op-text-secondary); }
    .wl-addr:hover { color:var(--op-brand,#5b5fc7); }
    .wl-unlink { flex:none; width:34px; height:34px; border-radius:9px; display:grid; place-items:center; background:transparent; border:1px solid var(--op-border, rgba(255,255,255,.1)); color:var(--op-text-secondary); cursor:pointer; }
    .wl-unlink:hover { color:#ef4444; border-color:#ef4444; }
    .wl-unlink:disabled { opacity:.4; cursor:not-allowed; }
    .wl-star { flex:none; width:34px; height:34px; border-radius:9px; display:grid; place-items:center; background:transparent; border:1px solid var(--op-border, rgba(255,255,255,.1)); color:var(--op-text-secondary); cursor:pointer; }
    .wl-star:hover { color:#22c55e; border-color:#22c55e; }
    .wl-star:disabled { opacity:.4; cursor:not-allowed; }
    .wl-primary-lock { flex:none; width:34px; height:34px; display:grid; place-items:center; color:#22c55e; }
    .wl-other { margin-top:14px; display:flex; flex-direction:column; gap:8px; }
    .wl-chain-btn { display:flex; align-items:center; gap:10px; width:100%; padding:10px 12px; border:1px solid var(--op-border, rgba(255,255,255,.1)); border-radius:11px; background:transparent; color:var(--op-text-primary); font-size:.88rem; cursor:pointer; }
    .wl-chain-btn:hover:not(:disabled) { border-color:var(--op-brand,#5b5fc7); }
    .wl-chain-btn > span:nth-child(2) { flex:1; text-align:left; }
    .wl-chain-btn:disabled { opacity:.6; cursor:not-allowed; }
    .wl-chain-ico { width:30px; height:30px; border-radius:8px; flex:none; display:grid; place-items:center; font-size:.6rem; font-weight:700; color:#fff; background:var(--tc); }
    .wl-soon { font-size:.62rem; font-weight:700; text-transform:uppercase; color:var(--op-text-secondary); background:var(--op-bg-surface-2, rgba(125,125,150,.12)); padding:2px 7px; border-radius:6px; }
    .wl-tg-row { cursor:default; }
    .wl-tg-row:hover { border-color:var(--op-border, rgba(255,255,255,.1)); }
    .wl-tg-widget { flex:none; display:flex; align-items:center; min-height:28px; }
    .wl-tg-widget iframe { color-scheme:normal; }
    .wl-add { margin-top:16px; width:100%; display:inline-flex; align-items:center; justify-content:center; gap:8px; background:linear-gradient(90deg,#5b5fc7,#06b6d4); color:#fff; border:0; border-radius:10px; padding:11px; font-weight:600; font-size:.9rem; cursor:pointer; }
    .wl-add:disabled { opacity:.5; cursor:not-allowed; }
    .wl-inline { margin-top:0; width:auto; padding:9px 16px; }
    .wl-hint { font-size:.78rem; color:var(--op-text-secondary); margin:10px 0 0; }
    .wl-linkflow { margin-top:16px; border:1px solid var(--op-border, rgba(255,255,255,.1)); border-radius:12px; padding:14px; background:var(--op-bg-surface-2, rgba(125,125,150,.05)); }
    .wl-linkflow-head { display:flex; align-items:center; gap:8px; font-weight:600; font-size:.9rem; color:var(--op-text-primary); margin-bottom:8px; }
    .wl-steps { margin:0 0 10px; padding-left:20px; color:var(--op-text-secondary); font-size:.83rem; display:flex; flex-direction:column; gap:5px; }
    .wl-steps b { color:var(--op-text-primary); }
    .wl-connected-now { font-size:.8rem; color:var(--op-text-secondary); margin-bottom:12px; }
    .wl-connected-now span { font-family:monospace; color:var(--op-text-primary); }
    .wl-connected-now em { color:#f59e0b; font-style:normal; }
    .wl-linkflow-actions { display:flex; gap:10px; justify-content:flex-end; align-items:center; }
    .wl-btn-ghost { background:transparent; border:1px solid var(--op-border, rgba(255,255,255,.12)); color:var(--op-text-secondary); border-radius:9px; padding:9px 14px; font-size:.85rem; cursor:pointer; }
    .wl-btn-ghost:hover { color:var(--op-text-primary); }
    .wl-btn-ghost:disabled { opacity:.5; cursor:not-allowed; }
    .wl-msg { font-size:.83rem; margin-top:10px; color:#ef4444; }
    .wl-msg.ok { color:#22c55e; }
    .wl-error { color:#ef4444; display:flex; gap:10px; align-items:center; font-size:.9rem; }
    .wl-error button, .wl-skel { }
    .wl-skel .sk { height:64px; border-radius:12px; margin-bottom:8px; background:linear-gradient(90deg, rgba(125,125,150,.08), rgba(125,125,150,.16), rgba(125,125,150,.08)); background-size:200% 100%; animation:sh 1.3s infinite; }
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

  meta(id: LinkedIdentity) { return TYPE_META[id.type] ?? { label: id.type, color: '#7e8298', short: '?' }; }

  shortId(s: string): string {
    return s.length > 16 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
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

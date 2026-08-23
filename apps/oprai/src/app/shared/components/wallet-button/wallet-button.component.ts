import {
  Component,
  inject,
  signal,
  HostListener,
  ElementRef,
  ViewChild,
  AfterViewChecked,
  OnDestroy,
  DestroyRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { WalletService } from '@core/services/wallet.service';
import { AuthService } from '@core/services/auth.service';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { LucideAngularModule } from 'lucide-angular';
import { TPipe } from '@core/i18n';

interface EvmProvider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
}

@Component({
  selector: 'app-wallet-button',
  standalone: true,
  imports: [CommonModule, TruncateAddressPipe, LucideAngularModule, TPipe],
  templateUrl: './wallet-button.component.html',
  styleUrl: './wallet-button.component.scss',
})
export class WalletButtonComponent implements AfterViewChecked, OnDestroy {
  private readonly el = inject(ElementRef);
  private readonly destroyRef = inject(DestroyRef);
  readonly walletService = inject(WalletService);
  readonly authService = inject(AuthService);

  readonly modalOpen = signal(false);
  readonly dropdownOpen = signal(false);
  readonly error = signal<string | null>(null);

  @ViewChild('modalOverlay') modalOverlayRef!: ElementRef<HTMLElement>;
  private modalMovedToBody = false;

  /** Snapshot taken when the modal opens (with a fresh Standard re-scan), so
   *  the list is stable while the user reads it and reflects wallets that
   *  finished initialising after page load. */
  private _walletSnapshot: import('@core/services/wallet.service').WalletInfo[] = [];

  get wallets(): import('@core/services/wallet.service').WalletInfo[] {
    return this._walletSnapshot;
  }

  // Which network the user is signing in with. OPRAI is Solana-native, so Solana
  // is the default; Ethereum (SIWE) is the alternative. The modal shows the
  // wallets for the SELECTED chain — a multichain wallet (Phantom, Backpack) can
  // appear under both, connecting with that chain's scheme (SIWS vs SIWE).
  readonly loginChain = signal<'solana' | 'ethereum'>('solana');

  // Wallets the Solana Wallet Standard registry surfaces that are NOT Solana
  // wallets: EVM-only (MetaMask, Rabby, …) and other-chain (Pontem = Aptos,
  // Leap = Cosmos). They must never appear on the Solana tab (clicking would run
  // SIWS and fail). EVM ones live on the Ethereum tab via EIP-6963; genuinely
  // unsupported chains (Aptos/Cosmos) show nowhere. A wallet that ALSO does
  // Solana (Phantom, Backpack) is NOT here — it stays on the Solana tab.
  private static readonly NON_SOLANA_WALLETS = new Set([
    'metamask', 'rabby', 'rainbow', 'zerion', 'pontem', 'leap', 'keplr',
    'petra', 'martian', 'trust wallet', 'trust', 'hashpack',
  ]);

  // Wallets that announce via EIP-6963 but are NOT Ethereum wallets — they're
  // other-chain wallets (Keplr/Leap = Cosmos, Pontem/Petra/Martian = Aptos,
  // HashPack = Hedera) that inject an EVM-shaped provider. Keep them off the
  // Ethereum tab. Leap is here AND in NON_SOLANA_WALLETS, so it shows nowhere.
  private static readonly NON_EVM_WALLETS = new Set([
    'keplr', 'leap', 'pontem', 'petra', 'martian', 'hashpack', 'cosmostation',
    'fewcha', 'nightly',
  ]);

  // Well-known EVM wallets, so the Ethereum tab can list the full set (detected
  // on top, the rest as install prompts) — like the Solana tab does. Icons fall
  // back to a letter-avatar when the asset/announced icon is missing or broken.
  private static readonly EVM_WALLETS: ReadonlyArray<{ name: string; url: string; icon: string }> = [
    { name: 'MetaMask', url: 'https://metamask.io/download/', icon: '/icons/wallets/metamask.svg' },
    { name: 'Rabby', url: 'https://rabby.io/', icon: '/icons/wallets/rabby.svg' },
    { name: 'Coinbase Wallet', url: 'https://www.coinbase.com/wallet/downloads', icon: '/icons/wallets/coinbase.svg' },
    { name: 'Rainbow', url: 'https://rainbow.me/', icon: '/icons/wallets/rainbow.svg' },
    { name: 'Trust Wallet', url: 'https://trustwallet.com/download', icon: '/icons/wallets/trust.svg' },
    { name: 'OKX Wallet', url: 'https://www.okx.com/web3', icon: '/icons/wallets/okx.png' },
    { name: 'Zerion', url: 'https://zerion.io/', icon: '/icons/wallets/zerion.svg' },
    { name: 'Phantom', url: 'https://phantom.app/', icon: '/icons/wallets/phantom.svg' },
  ];

  // The one EIP-6963 icon that renders broken (Phantom ships a dark square).
  // Everything else uses its announced icon, which is fine.
  private static readonly ICON_OVERRIDE: Record<string, string> = {
    phantom: '/icons/wallets/phantom.svg',
  };

  get detectedWallets(): import('@core/services/wallet.service').WalletInfo[] {
    return this._walletSnapshot.filter(
      (w) =>
        w.detected &&
        !WalletButtonComponent.NON_SOLANA_WALLETS.has(w.name.trim().toLowerCase()),
    );
  }

  /** EIP-6963 wallets that are actually EVM (Cosmos/Aptos/Hedera filtered out),
   *  with a good icon (Phantom's broken square swapped for the bundled asset). */
  get evmDetectedWallets(): Array<{ uuid: string; name: string; icon: string; provider: EvmProvider }> {
    return this.evmWallets()
      .filter((w) => !WalletButtonComponent.NON_EVM_WALLETS.has(w.name.trim().toLowerCase()))
      .map((w) => ({ ...w, icon: WalletButtonComponent.ICON_OVERRIDE[w.name.trim().toLowerCase()] ?? w.icon }));
  }

  /** Normalize a wallet name for comparison: lowercase + drop a trailing
   *  " Wallet" so detected "Rabby Wallet" matches curated "Rabby" and we don't
   *  list it twice (once detected, once as an install prompt). */
  private static normName(name: string): string {
    return name.trim().toLowerCase().replace(/\s+wallet$/, '');
  }

  /** Known EVM wallets the user does NOT already have detected — shown as
   *  install prompts under the detected ones so the Ethereum tab lists the full
   *  set, each with its real bundled brand icon. */
  get installableEvmWallets(): Array<{ name: string; url: string; icon: string }> {
    const have = new Set(this.evmDetectedWallets.map((w) => WalletButtonComponent.normName(w.name)));
    return WalletButtonComponent.EVM_WALLETS.filter(
      (w) => !have.has(WalletButtonComponent.normName(w.name)),
    );
  }

  get installableWallets(): import('@core/services/wallet.service').WalletInfo[] {
    return this._walletSnapshot.filter((w) => !w.detected);
  }

  ngAfterViewChecked(): void {
    if (this.modalOpen() && this.modalOverlayRef && !this.modalMovedToBody) {
      document.body.appendChild(this.modalOverlayRef.nativeElement);
      this.modalMovedToBody = true;
    }
  }

  ngOnDestroy(): void {
    this.removeModalFromBody();
  }

  // EVM wallets discovered via EIP-6963 — lets the user sign in with Ethereum
  // (SIWE) as an alternative to a Solana wallet. Populated when the modal opens.
  readonly evmWallets = signal<Array<{ uuid: string; name: string; icon: string; provider: EvmProvider }>>([]);

  openModal(): void {
    this.error.set(null);
    // Re-scan the Standard registry as the modal opens so a wallet that
    // registered late is present, and freeze the result for the modal's life.
    this._walletSnapshot = this.walletService.getWallets(true);
    this.modalOpen.set(true);
    this.discoverEvmWallets();
  }

  private discoverEvmWallets(): void {
    this.evmWallets.set([]);
    const found = new Map<string, { uuid: string; name: string; icon: string; provider: EvmProvider }>();
    const handler = (e: Event) => {
      const d = (e as CustomEvent).detail as
        | { info?: { uuid: string; name: string; icon: string }; provider?: EvmProvider }
        | undefined;
      if (d?.info && d.provider) {
        found.set(d.info.uuid, { uuid: d.info.uuid, name: d.info.name, icon: d.info.icon, provider: d.provider });
        this.evmWallets.set([...found.values()]);
      }
    };
    window.addEventListener('eip6963:announceProvider', handler);
    window.dispatchEvent(new Event('eip6963:requestProvider'));
    setTimeout(() => window.removeEventListener('eip6963:announceProvider', handler), 400);
  }

  /** Switch to the Ethereum tab and (re-)run EIP-6963 discovery, so a wallet
   *  that announced late is present even if the modal opened before it did. */
  selectEthereum(): void {
    this.loginChain.set('ethereum');
    this.discoverEvmWallets();
  }

  /** A wallet icon that is missing (no bundled asset) or broken (some EIP-6963
   *  providers ship a malformed / dark-square data-URI — Phantom's did) is
   *  replaced with a clean letter-avatar built from the wallet's name, so every
   *  row looks intentional instead of a torn image. */
  onIconError(e: Event): void {
    const img = e.target as HTMLImageElement;
    if (img.dataset['fallback']) return; // already swapped — don't loop
    img.dataset['fallback'] = '1';
    const name = (img.alt || '?').trim();
    const letter = (name.charAt(0) || '?').toUpperCase();
    // Deterministic brand-ish hue from the name so different wallets differ.
    let h = 0;
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
    img.src =
      'data:image/svg+xml;utf8,' +
      encodeURIComponent(
        `<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'>` +
          `<rect width='32' height='32' rx='9' fill='hsl(${h},55%,48%)'/>` +
          `<text x='16' y='21' font-family='sans-serif' font-size='15' font-weight='700' ` +
          `fill='white' text-anchor='middle'>${letter}</text>` +
          `</svg>`,
      );
  }

  async connectEvm(w: { name: string; provider: EvmProvider }): Promise<void> {
    this.error.set(null);
    try {
      const accounts = (await w.provider.request({ method: 'eth_requestAccounts' })) as string[];
      const address = accounts?.[0];
      if (!address) throw new Error('No account selected');
      this.closeModal();
      await this.authService.authenticateEvm(w.provider, address);
    } catch (err: unknown) {
      const rejected = (err as { code?: number })?.code === 4001;
      this.error.set(rejected ? 'Signature request was rejected.' : this.friendlyError(err));
    }
  }

  closeModal(): void {
    this.modalOpen.set(false);
    this.removeModalFromBody();
  }

  toggleDropdown(): void {
    this.dropdownOpen.update((v) => !v);
  }

  closeDropdown(): void {
    this.dropdownOpen.set(false);
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.modalOpen()) this.closeModal();
    if (this.dropdownOpen()) this.closeDropdown();
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (this.dropdownOpen() && !this.el.nativeElement.contains(event.target)) {
      this.closeDropdown();
    }
  }

  onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('wallet-modal-overlay')) {
      this.closeModal();
    }
  }

  async connectWallet(walletName: string): Promise<void> {
    this.error.set(null);
    this.closeModal();

    try {
      await this.walletService.connect(walletName);

      // takeUntilDestroyed ensures the subscription is cleaned up if the
      // component is destroyed before authentication completes.
      this.authService
        .authenticate()
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          error: (err) => this.error.set(this.friendlyError(err)),
        });
    } catch (err: unknown) {
      this.error.set(err instanceof Error ? err.message : 'Failed to connect wallet');
    }
  }

  openInstallUrl(url: string): void {
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  async disconnect(): Promise<void> {
    this.closeDropdown();
    await this.walletService.disconnect();
    this.authService.logout();
  }

  private friendlyError(err: unknown): string {
    if (err instanceof HttpErrorResponse && err.status === 0) {
      // status 0 = offline / DNS / CORS — phrase it as a connectivity hint
      // without leaking that we have a backend at all.
      return 'Connection lost. Check your internet and try again.';
    }
    if (err instanceof HttpErrorResponse && err.status >= 500) {
      return 'We\'re having trouble right now. Please try again in a moment.';
    }
    if (err instanceof Error) {
      // Wallet-adapter / signature-rejection messages reach the user as-is —
      // they're already user-readable ("User rejected the request", etc.)
      return err.message;
    }
    return 'Couldn\'t sign in. Please try again.';
  }

  private removeModalFromBody(): void {
    if (
      this.modalMovedToBody &&
      this.modalOverlayRef?.nativeElement?.parentNode === document.body
    ) {
      document.body.removeChild(this.modalOverlayRef.nativeElement);
    }
    this.modalMovedToBody = false;
  }
}

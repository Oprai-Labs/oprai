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

  // Wallets the Solana Wallet Standard registry surfaces that CANNOT sign a
  // Solana SIWS login: EVM-only (MetaMask, Rabby, …) and other-chain wallets
  // (Pontem = Aptos, Leap = Cosmos). Listing them under "Detected" made clicking
  // one run SIWS and fail. EVM ones re-appear under "Or sign in with Ethereum"
  // (EIP-6963); the truly-unsupported ones just don't show.
  private static readonly NON_SOLANA_WALLETS = new Set([
    'metamask', 'rabby', 'rainbow', 'zerion', 'pontem', 'leap', 'keplr',
    'petra', 'martian', 'trust wallet', 'trust',
  ]);

  get detectedWallets(): import('@core/services/wallet.service').WalletInfo[] {
    const evmNames = new Set(this.evmWallets().map((w) => w.name.toLowerCase()));
    return this._walletSnapshot.filter(
      (w) =>
        w.detected &&
        !WalletButtonComponent.NON_SOLANA_WALLETS.has(w.name.trim().toLowerCase()) &&
        !evmNames.has(w.name.trim().toLowerCase()),
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

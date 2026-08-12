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

@Component({
  selector: 'app-wallet-button',
  standalone: true,
  imports: [CommonModule, TruncateAddressPipe, LucideAngularModule],
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

  get detectedWallets(): import('@core/services/wallet.service').WalletInfo[] {
    return this._walletSnapshot.filter((w) => w.detected);
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

  openModal(): void {
    this.error.set(null);
    // Re-scan the Standard registry as the modal opens so a wallet that
    // registered late is present, and freeze the result for the modal's life.
    this._walletSnapshot = this.walletService.getWallets(true);
    this.modalOpen.set(true);
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

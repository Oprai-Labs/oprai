import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import {
  MultiWalletService,
  LinkedWallet,
} from '@core/services/multi-wallet.service';
import { WalletService } from '@core/services/wallet.service';

@Component({
  selector: 'app-wallet-switcher',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  template: `
    <div class="wallet-switcher">
      <!-- Current wallet display -->
      <button class="ws-trigger" (click)="toggleDropdown()" type="button">
        <div class="ws-current">
          @if (activeWallet()) {
            <div class="ws-avatar" [class.ws-avatar--primary]="activeWallet()!.isPrimary">
              {{ activeWallet()!.nickname?.charAt(0) ?? activeWallet()!.address.slice(0, 2) }}
            </div>
            <div class="ws-info">
              <span class="ws-name">{{ activeWallet()!.nickname ?? shortenAddress(activeWallet()!.address) }}</span>
              <span class="ws-type">{{ activeWallet()!.walletType }}</span>
            </div>
          } @else if (walletService.connected()) {
            <div class="ws-avatar ws-avatar--connected">
              {{ walletService.shortAddress()?.charAt(0) ?? '?' }}
            </div>
            <div class="ws-info">
              <span class="ws-name">{{ walletService.shortAddress() }}</span>
              <span class="ws-type">{{ walletService.walletName() }}</span>
            </div>
          } @else {
            <div class="ws-avatar ws-avatar--disconnected">
              <lucide-icon name="wallet" [size]="16" />
            </div>
            <div class="ws-info">
              <span class="ws-name">No wallet</span>
              <span class="ws-type">Connect to start</span>
            </div>
          }
        </div>
        <lucide-icon [name]="showDropdown() ? 'chevron-up' : 'chevron-down'" [size]="16" class="ws-chevron" />
      </button>

      <!-- Dropdown -->
      @if (showDropdown()) {
        <div class="ws-dropdown" (click)="$event.stopPropagation()">
          <!-- Header -->
          <div class="ws-header">
            <span>My Wallets</span>
            <span class="ws-count">{{ multiWallet.walletCount() }}/{{ maxWallets }}</span>
          </div>

          <!-- Wallet list -->
          <div class="ws-list">
            @for (wallet of multiWallet.linkedWallets(); track wallet.id) {
              <button
                class="ws-wallet-item"
                [class.ws-wallet-item--active]="multiWallet.activeWallet()?.id === wallet.id"
                (click)="selectWallet(wallet)"
                type="button">
                <div class="ws-avatar" [class.ws-avatar--primary]="wallet.isPrimary">
                  {{ wallet.nickname?.charAt(0) ?? wallet.address.slice(0, 2) }}
                </div>
                <div class="ws-wallet-info">
                  <span class="ws-wallet-name">
                    {{ wallet.nickname ?? shortenAddress(wallet.address) }}
                    @if (wallet.isPrimary) {
                      <span class="ws-badge">Primary</span>
                    }
                  </span>
                  <span class="ws-wallet-address">{{ shortenAddress(wallet.address) }}</span>
                </div>
                <div class="ws-wallet-permission">
                  <span class="ws-permission" [class]="'ws-permission--' + wallet.permissions">
                    {{ wallet.permissions }}
                  </span>
                </div>
                @if (multiWallet.activeWallet()?.id === wallet.id) {
                  <lucide-icon name="check" [size]="14" class="ws-check" />
                }
              </button>
            } @empty {
              <div class="ws-empty">
                <lucide-icon name="wallet" [size]="24" />
                <span>No linked wallets</span>
                <p>Connect a wallet to get started</p>
              </div>
            }
          </div>

          <!-- Actions -->
          <div class="ws-actions">
            @if (multiWallet.canLinkMore() && walletService.connected()) {
              @if (isCurrentWalletLinked()) {
                <button class="ws-action-btn ws-action-btn--secondary" (click)="openSettings()" type="button">
                  <lucide-icon name="settings" [size]="14" />
                  Manage Wallets
                </button>
              } @else {
                <button class="ws-action-btn" (click)="linkCurrentWallet()" type="button" [disabled]="linking()">
                  @if (linking()) {
                    <span class="ws-spinner"></span>
                  } @else {
                    <lucide-icon name="plus" [size]="14" />
                  }
                  Link Current Wallet
                </button>
              }
            }

            @if (!walletService.connected()) {
              <button class="ws-action-btn" (click)="walletService.connect()" type="button">
                <lucide-icon name="link" [size]="14" />
                Connect Wallet
              </button>
            }
          </div>

          <!-- Quick actions for active wallet -->
          @if (activeWallet()) {
            <div class="ws-quick-actions">
              <button class="ws-quick-btn" (click)="setPrimary()" type="button"
                      [disabled]="activeWallet()!.isPrimary">
                <lucide-icon name="star" [size]="12" />
                Set Primary
              </button>
              <button class="ws-quick-btn" (click)="editNickname()" type="button">
                <lucide-icon name="pencil" [size]="12" />
                Rename
              </button>
              <button class="ws-quick-btn ws-quick-btn--danger" (click)="unlinkWallet()" type="button">
                <lucide-icon name="unlink" [size]="12" />
                Unlink
              </button>
            </div>
          }
        </div>
      }
    </div>

    <!-- Nickname edit modal -->
    @if (showNicknameModal()) {
      <div class="ws-modal-overlay" (click)="showNicknameModal.set(false)">
        <div class="ws-modal" (click)="$event.stopPropagation()">
          <div class="ws-modal-header">
            <span>Rename Wallet</span>
            <button class="ws-modal-close" (click)="showNicknameModal.set(false)" type="button">
              <lucide-icon name="x" [size]="16" />
            </button>
          </div>
          <div class="ws-modal-body">
            <input
              type="text"
              class="ws-input"
              placeholder="Enter nickname..."
              [value]="nicknameInput()"
              (input)="nicknameInput.set($any($event.target).value)"
              maxlength="20"
            />
          </div>
          <div class="ws-modal-actions">
            <button class="ws-modal-btn ws-modal-btn--cancel" (click)="showNicknameModal.set(false)" type="button">
              Cancel
            </button>
            <button class="ws-modal-btn" (click)="saveNickname()" type="button">
              Save
            </button>
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    .wallet-switcher {
      position: relative;
    }

    .ws-trigger {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: var(--op-radius-md);
      border: 1px solid var(--op-border-subtle);
      background: var(--op-bg-surface-1);
      cursor: pointer;
      transition: all 0.15s;
    }

    .ws-trigger:hover {
      background: var(--op-bg-surface-2);
      border-color: var(--op-border-default);
    }

    .ws-current {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .ws-avatar {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: var(--op-gradient);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
    }

    .ws-avatar--primary {
      background: linear-gradient(135deg, #f59e0b, #d97706);
    }

    .ws-avatar--connected {
      background: var(--op-gain);
    }

    .ws-avatar--disconnected {
      background: var(--op-bg-surface-3);
      color: var(--op-text-tertiary);
    }

    .ws-info {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      min-width: 0;
    }

    .ws-name {
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--op-text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .ws-type {
      font-size: 0.65rem;
      color: var(--op-text-tertiary);
    }

    .ws-chevron {
      color: var(--op-text-tertiary);
      flex-shrink: 0;
    }

    .ws-dropdown {
      position: absolute;
      top: calc(100% + 8px);
      right: 0;
      width: 280px;
      background: var(--op-bg-surface-1);
      border: 1px solid var(--op-border-subtle);
      border-radius: var(--op-radius-lg);
      box-shadow: var(--op-shadow-lg);
      z-index: 100;
      overflow: hidden;
      animation: ws-slide 0.15s ease-out;
    }

    @keyframes ws-slide {
      from { opacity: 0; transform: translateY(-8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .ws-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 14px;
      border-bottom: 1px solid var(--op-border-subtle);
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--op-text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .ws-count {
      color: var(--op-text-tertiary);
      font-weight: 500;
    }

    .ws-list {
      max-height: 240px;
      overflow-y: auto;
    }

    .ws-wallet-item {
      display: flex;
      align-items: center;
      gap: 10px;
      width: 100%;
      padding: 10px 14px;
      border: none;
      background: transparent;
      cursor: pointer;
      transition: background 0.1s;
      text-align: left;
    }

    .ws-wallet-item:hover {
      background: var(--op-bg-surface-2);
    }

    .ws-wallet-item--active {
      background: color-mix(in srgb, var(--op-brand) 8%, transparent);
    }

    .ws-wallet-info {
      flex: 1;
      min-width: 0;
    }

    .ws-wallet-name {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 0.8rem;
      font-weight: 500;
      color: var(--op-text-primary);
    }

    .ws-badge {
      font-size: 0.6rem;
      padding: 2px 5px;
      border-radius: 4px;
      background: linear-gradient(135deg, #f59e0b, #d97706);
      color: #fff;
      font-weight: 600;
      text-transform: uppercase;
    }

    .ws-wallet-address {
      display: block;
      font-size: 0.7rem;
      color: var(--op-text-tertiary);
      font-family: var(--op-font-mono, monospace);
    }

    .ws-wallet-permission {
      flex-shrink: 0;
    }

    .ws-permission {
      font-size: 0.6rem;
      padding: 2px 6px;
      border-radius: 4px;
      background: var(--op-bg-surface-3);
      color: var(--op-text-tertiary);
      text-transform: uppercase;
      font-weight: 600;
    }

    .ws-permission--sign {
      background: color-mix(in srgb, #3b82f6 15%, transparent);
      color: #3b82f6;
    }

    .ws-permission--full {
      background: color-mix(in srgb, var(--op-gain) 15%, transparent);
      color: var(--op-gain);
    }

    .ws-check {
      color: var(--op-brand);
      flex-shrink: 0;
    }

    .ws-empty {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      padding: 32px 16px;
      color: var(--op-text-tertiary);
      text-align: center;
    }

    .ws-empty span {
      font-size: 0.85rem;
      font-weight: 500;
    }

    .ws-empty p {
      font-size: 0.75rem;
      margin: 0;
    }

    .ws-actions {
      padding: 10px 14px;
      border-top: 1px solid var(--op-border-subtle);
    }

    .ws-action-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      width: 100%;
      padding: 10px;
      border-radius: var(--op-radius-sm);
      border: none;
      background: var(--op-brand);
      color: #fff;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }

    .ws-action-btn:hover:not(:disabled) {
      filter: brightness(1.1);
    }

    .ws-action-btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }

    .ws-action-btn--secondary {
      background: var(--op-bg-surface-3);
      color: var(--op-text-primary);
    }

    .ws-spinner {
      width: 14px;
      height: 14px;
      border: 2px solid rgba(255,255,255,0.3);
      border-top-color: #fff;
      border-radius: 50%;
      animation: spin 0.6s linear infinite;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    .ws-quick-actions {
      display: flex;
      gap: 6px;
      padding: 10px 14px;
      border-top: 1px solid var(--op-border-subtle);
    }

    .ws-quick-btn {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      padding: 8px;
      border-radius: var(--op-radius-sm);
      border: 1px solid var(--op-border-subtle);
      background: transparent;
      color: var(--op-text-secondary);
      font-size: 0.7rem;
      cursor: pointer;
      transition: all 0.15s;
    }

    .ws-quick-btn:hover:not(:disabled) {
      background: var(--op-bg-surface-2);
      color: var(--op-text-primary);
    }

    .ws-quick-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .ws-quick-btn--danger {
      border-color: color-mix(in srgb, var(--op-loss) 30%, transparent);
      color: var(--op-loss);
    }

    .ws-quick-btn--danger:hover {
      background: color-mix(in srgb, var(--op-loss) 10%, transparent);
    }

    /* Modal */
    .ws-modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(4px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 200;
    }

    .ws-modal {
      background: var(--op-bg-surface-1);
      border: 1px solid var(--op-border-subtle);
      border-radius: var(--op-radius-lg);
      width: 100%;
      max-width: 320px;
      box-shadow: var(--op-shadow-lg);
    }

    .ws-modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 16px;
      border-bottom: 1px solid var(--op-border-subtle);
      font-size: 0.9rem;
      font-weight: 600;
      color: var(--op-text-primary);
    }

    .ws-modal-close {
      width: 28px;
      height: 28px;
      border-radius: 6px;
      border: none;
      background: transparent;
      color: var(--op-text-tertiary);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .ws-modal-close:hover {
      background: var(--op-bg-surface-2);
      color: var(--op-text-primary);
    }

    .ws-modal-body {
      padding: 16px;
    }

    .ws-input {
      width: 100%;
      padding: 10px 12px;
      border-radius: var(--op-radius-sm);
      border: 1px solid var(--op-border-subtle);
      background: var(--op-bg-surface-2);
      color: var(--op-text-primary);
      font-size: 0.85rem;
    }

    .ws-input:focus {
      outline: none;
      border-color: var(--op-brand);
    }

    .ws-modal-actions {
      display: flex;
      gap: 10px;
      padding: 14px 16px;
      border-top: 1px solid var(--op-border-subtle);
    }

    .ws-modal-btn {
      flex: 1;
      padding: 10px;
      border-radius: var(--op-radius-sm);
      border: none;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }

    .ws-modal-btn--cancel {
      background: var(--op-bg-surface-2);
      color: var(--op-text-secondary);
    }

    .ws-modal-btn--cancel:hover {
      background: var(--op-bg-surface-3);
      color: var(--op-text-primary);
    }

    .ws-modal-btn:not(.ws-modal-btn--cancel) {
      background: var(--op-brand);
      color: #fff;
    }

    .ws-modal-btn:not(.ws-modal-btn--cancel):hover {
      filter: brightness(1.1);
    }
  `],
})
export class WalletSwitcherComponent {
  readonly multiWallet = inject(MultiWalletService);
  readonly walletService = inject(WalletService);

  readonly showDropdown = signal(false);
  readonly linking = signal(false);
  readonly showNicknameModal = signal(false);
  readonly nicknameInput = signal('');

  readonly maxWallets = 10;

  readonly activeWallet = this.multiWallet.activeWallet;

  // Output events
  readonly connectWallet = new EventTarget();

  toggleDropdown(): void {
    this.showDropdown.update(v => !v);
  }

  shortenAddress(address: string): string {
    return `${address.slice(0, 4)}...${address.slice(-4)}`;
  }

  isCurrentWalletLinked(): boolean {
    const connected = this.walletService.publicKey();
    if (!connected) return false;
    return this.multiWallet.linkedWallets().some(w => w.address === connected);
  }

  async linkCurrentWallet(): Promise<void> {
    const address = this.walletService.publicKey();
    const walletName = this.walletService.walletName();
    if (!address) return;

    this.linking.set(true);
    try {
      await this.multiWallet.linkCurrentWallet(
        `My ${walletName ?? 'Wallet'}`,
        'full'
      );
    } finally {
      this.linking.set(false);
    }
  }

  async selectWallet(wallet: LinkedWallet): Promise<void> {
    // Check if this is the currently connected wallet
    const connectedAddress = this.walletService.publicKey();

    if (wallet.address === connectedAddress) {
      // Already connected to this wallet, just set as active
      this.multiWallet.setActiveWallet(wallet.id);
      this.showDropdown.set(false);
      return;
    }

    // Need to switch wallet connection
    // This requires user to connect the other wallet
    // For now, show a message
    alert(`Please connect ${wallet.walletType} wallet with address ${this.shortenAddress(wallet.address)} to switch.`);
  }

  async setPrimary(): Promise<void> {
    const active = this.activeWallet();
    if (!active || active.isPrimary) return;

    await this.multiWallet.setPrimaryWallet(active.id);
  }

  editNickname(): void {
    const active = this.activeWallet();
    if (!active) return;

    this.nicknameInput.set(active.nickname ?? '');
    this.showNicknameModal.set(true);
  }

  async saveNickname(): Promise<void> {
    const active = this.activeWallet();
    if (!active) return;

    const nickname = this.nicknameInput().trim();
    await this.multiWallet.updateNickname(active.id, nickname || '');
    this.showNicknameModal.set(false);
  }

  async unlinkWallet(): Promise<void> {
    const active = this.activeWallet();
    if (!active) return;

    if (confirm(`Unlink wallet ${active.nickname ?? this.shortenAddress(active.address)}?`)) {
      await this.multiWallet.unlinkWallet(active.id);
    }
  }

  openSettings(): void {
    this.showDropdown.set(false);
    // Navigate to settings/wallets page
    // this.router.navigate(['/settings/wallets']);
  }
}

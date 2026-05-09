import { Injectable, signal, computed, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { WalletService } from './wallet.service';
import { environment } from '../../../environments/environment';

// ─── Types ───────────────────────────────────────────────────────────────────

export type WalletPermission = 'view' | 'sign' | 'full';

export interface LinkedWallet {
  id: string;
  address: string;
  nickname: string | null;
  walletType: string; // 'phantom', 'solflare', etc.
  permissions: WalletPermission;
  isPrimary: boolean;
  isActive: boolean;
  lastUsed: string | null;
  linkedAt: string;
}

export interface WalletBalanceSummary {
  address: string;
  solBalance: number;
  tokenCount: number;
  totalUsdValue: number;
}

// ─── Service ─────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class MultiWalletService {
  private readonly http = inject(HttpClient);
  private readonly walletService = inject(WalletService);

  // Storage key for linked wallets
  private readonly STORAGE_KEY = 'oprai-linked-wallets';
  private readonly MAX_WALLETS = 10;

  // ─── Signals ───────────────────────────────────────────────────────────────

  /** All linked wallets for the current user */
  readonly linkedWallets = signal<LinkedWallet[]>([]);

  /** Currently active wallet (for signing transactions) */
  readonly activeWallet = signal<LinkedWallet | null>(null);

  /** Loading state */
  readonly loading = signal(false);

  /** Error state */
  readonly error = signal<string | null>(null);

  // ─── Computed ──────────────────────────────────────────────────────────────

  /** Number of linked wallets */
  readonly walletCount = computed(() => this.linkedWallets().length);

  /** Can link more wallets? */
  readonly canLinkMore = computed(() => this.walletCount() < this.MAX_WALLETS);

  /** Primary wallet */
  readonly primaryWallet = computed(() =>
    this.linkedWallets().find(w => w.isPrimary) ?? null
  );

  /** Wallets grouped by permission level */
  readonly walletsByPermission = computed(() => {
    const wallets = this.linkedWallets();
    return {
      full: wallets.filter(w => w.permissions === 'full'),
      sign: wallets.filter(w => w.permissions === 'sign'),
      view: wallets.filter(w => w.permissions === 'view'),
    };
  });

  /** Active wallet address (for transaction signing) */
  readonly activeAddress = computed(() => this.activeWallet()?.address ?? null);

  /** Check if currently connected wallet is in linked list */
  readonly isConnectedWalletLinked = computed(() => {
    const connected = this.walletService.publicKey();
    if (!connected) return false;
    return this.linkedWallets().some(w => w.address === connected);
  });

  // ─── Initialization ────────────────────────────────────────────────────────

  constructor() {
    this.loadFromStorage();
    this.syncWithConnectedWallet();
  }

  /** Load linked wallets from localStorage */
  private loadFromStorage(): void {
    try {
      const stored = localStorage.getItem(this.STORAGE_KEY);
      if (stored) {
        const wallets = JSON.parse(stored) as LinkedWallet[];
        this.linkedWallets.set(wallets);

        // Set active wallet to primary or first
        const active = wallets.find(w => w.isActive) ?? wallets.find(w => w.isPrimary) ?? wallets[0];
        if (active) {
          this.activeWallet.set(active);
        }
      }
    } catch (err) {
      console.error('[MultiWallet] Failed to load from storage:', err);
    }
  }

  /** Save linked wallets to localStorage */
  private saveToStorage(): void {
    try {
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(this.linkedWallets()));
    } catch (err) {
      console.error('[MultiWallet] Failed to save to storage:', err);
    }
  }

  /** Sync active wallet with currently connected wallet */
  private syncWithConnectedWallet(): void {
    // Watch for wallet connection changes
    const connectedAddress = this.walletService.publicKey();
    if (connectedAddress) {
      const linked = this.linkedWallets().find(w => w.address === connectedAddress);
      if (linked && !linked.isActive) {
        this.setActiveWallet(linked.id);
      }
    }
  }

  // ─── Public Methods ────────────────────────────────────────────────────────

  /**
   * Link the currently connected wallet to the user's account
   */
  async linkCurrentWallet(
    nickname: string | null = null,
    permissions: WalletPermission = 'full'
  ): Promise<LinkedWallet> {
    const address = this.walletService.publicKey();
    const walletName = this.walletService.walletName();

    if (!address) {
      throw new Error('No wallet connected');
    }

    if (!this.canLinkMore()) {
      throw new Error(`Maximum ${this.MAX_WALLETS} wallets allowed`);
    }

    // Check if already linked
    const existing = this.linkedWallets().find(w => w.address === address);
    if (existing) {
      throw new Error('Wallet already linked');
    }

    const newWallet: LinkedWallet = {
      id: this.generateId(),
      address,
      nickname: nickname ?? this.generateNickname(walletName),
      walletType: walletName?.toLowerCase() ?? 'unknown',
      permissions,
      isPrimary: this.linkedWallets().length === 0, // First wallet is primary
      isActive: true,
      lastUsed: new Date().toISOString(),
      linkedAt: new Date().toISOString(),
    };

    // Deactivate other wallets
    const updated = this.linkedWallets().map(w => ({ ...w, isActive: false }));
    updated.push(newWallet);

    this.linkedWallets.set(updated);
    this.activeWallet.set(newWallet);
    this.saveToStorage();

    // Sync with backend (if available)
    await this.syncWithBackend('link', newWallet);

    return newWallet;
  }

  /**
   * Unlink a wallet from the user's account
   */
  async unlinkWallet(walletId: string): Promise<void> {
    const wallets = this.linkedWallets();
    const wallet = wallets.find(w => w.id === walletId);

    if (!wallet) {
      throw new Error('Wallet not found');
    }

    if (wallet.isPrimary && wallets.length > 1) {
      throw new Error('Cannot unlink primary wallet. Set another as primary first.');
    }

    const updated = wallets.filter(w => w.id !== walletId);

    // If we removed the active wallet, set a new one
    if (this.activeWallet()?.id === walletId && updated.length > 0) {
      updated[0] = { ...updated[0], isActive: true };
      this.activeWallet.set(updated[0]);
    }

    this.linkedWallets.set(updated);
    this.saveToStorage();

    // Sync with backend
    await this.syncWithBackend('unlink', wallet);
  }

  /**
   * Set a wallet as the active wallet for transactions
   */
  setActiveWallet(walletId: string): void {
    const wallets = this.linkedWallets();
    const wallet = wallets.find(w => w.id === walletId);

    if (!wallet) {
      throw new Error('Wallet not found');
    }

    const updated = wallets.map(w => ({
      ...w,
      isActive: w.id === walletId,
      lastUsed: w.id === walletId ? new Date().toISOString() : w.lastUsed,
    }));

    this.linkedWallets.set(updated);
    this.activeWallet.set({ ...wallet, isActive: true, lastUsed: new Date().toISOString() });
    this.saveToStorage();
  }

  /**
   * Set a wallet as the primary wallet
   */
  setPrimaryWallet(walletId: string): void {
    const wallets = this.linkedWallets();
    const wallet = wallets.find(w => w.id === walletId);

    if (!wallet) {
      throw new Error('Wallet not found');
    }

    const updated = wallets.map(w => ({
      ...w,
      isPrimary: w.id === walletId,
    }));

    this.linkedWallets.set(updated);
    this.saveToStorage();
  }

  /**
   * Update wallet permissions
   */
  updatePermissions(walletId: string, permissions: WalletPermission): void {
    const wallets = this.linkedWallets();
    const updated = wallets.map(w =>
      w.id === walletId ? { ...w, permissions } : w
    );

    this.linkedWallets.set(updated);
    this.saveToStorage();
  }

  /**
   * Update wallet nickname
   */
  updateNickname(walletId: string, nickname: string): void {
    const wallets = this.linkedWallets();
    const updated = wallets.map(w =>
      w.id === walletId ? { ...w, nickname } : w
    );

    this.linkedWallets.set(updated);
    this.saveToStorage();
  }

  /**
   * Get wallet by address
   */
  getWalletByAddress(address: string): LinkedWallet | undefined {
    return this.linkedWallets().find(w => w.address === address);
  }

  /**
   * Check if wallet has permission
   */
  hasPermission(walletId: string, requiredPermission: WalletPermission): boolean {
    const wallet = this.linkedWallets().find(w => w.id === walletId);
    if (!wallet) return false;

    const permissionLevels: WalletPermission[] = ['view', 'sign', 'full'];
    const walletLevel = permissionLevels.indexOf(wallet.permissions);
    const requiredLevel = permissionLevels.indexOf(requiredPermission);

    return walletLevel >= requiredLevel;
  }

  /**
   * Get aggregated balance across all wallets
   */
  async getAggregatedBalance(): Promise<WalletBalanceSummary[]> {
    const wallets = this.linkedWallets();

    // For now, return empty array - would need backend support
    // to query balances for non-connected wallets
    const summaries: WalletBalanceSummary[] = [];

    for (const wallet of wallets) {
      // Only get balance for currently connected wallet
      if (wallet.address === this.walletService.publicKey()) {
        summaries.push({
          address: wallet.address,
          solBalance: 0, // Would get from portfolio service
          tokenCount: 0,
          totalUsdValue: 0,
        });
      }
    }

    return summaries;
  }

  /**
   * Check if we should prompt user to link wallet
   */
  shouldPromptLink(): boolean {
    const connected = this.walletService.publicKey();
    if (!connected) return false;

    const isLinked = this.linkedWallets().some(w => w.address === connected);
    return !isLinked && this.canLinkMore();
  }

  // ─── Helpers ───────────────────────────────────────────────────────────────

  private generateId(): string {
    return `wallet_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
  }

  private generateNickname(walletName: string | null): string {
    const count = this.linkedWallets().length + 1;
    const type = walletName ?? 'Wallet';
    return `${type} ${count}`;
  }

  private async syncWithBackend(action: 'link' | 'unlink', wallet: LinkedWallet): Promise<void> {
    try {
      // This would sync with the backend API when available
      // For now, we just use localStorage
      if (environment.production) {
        const endpoint = `${environment.apiUrl}/wallets/${action}`;
        await firstValueFrom(this.http.post(endpoint, { walletId: wallet.id, address: wallet.address }));
      }
    } catch (err) {
      console.warn('[MultiWallet] Backend sync failed (using localStorage):', err);
    }
  }
}
